"""Optional, read-only adapters producing typed evidence only."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import re
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping, Protocol, Sequence, TypeVar, cast

from .const import (
    CONF_FRIGATE_ENABLED,
    CONF_FRIGATE_MQTT_TOPIC,
    CONF_HA_SOURCES_ENABLED,
)
from .agent_config_validation import validate_mqtt_topic


_LOGGER = logging.getLogger(__name__)

_ListenerT = TypeVar("_ListenerT", bound=Callable[..., object])


def _event_loop_callback(func: _ListenerT) -> _ListenerT:
    """Mark a listener like ``homeassistant.core.callback`` (F2).

    Without the marker Home Assistant runs a plain function in the executor,
    where the evidence sink is not thread safe. Setting the same attribute
    keeps this module free of a Home Assistant import.
    """
    setattr(func, "_hass_callback", True)
    return func


class _EntryLike(Protocol):
    options: Mapping[str, object]


class _BusLike(Protocol):
    def async_listen(self, event_type: str, callback: Callable[..., object]) -> Callable[[], None]: ...


class _HassLike(Protocol):
    bus: _BusLike


class _EventLike(Protocol):
    data: Mapping[str, object]
    time_fired: datetime


class _MessageLike(Protocol):
    payload: str | bytes


class _MqttLike(Protocol):
    def async_subscribe(
        self,
        hass: object,
        topic: str,
        callback: Callable[..., object],
        *,
        qos: int,
    ) -> Awaitable[Callable[[], None]]: ...


class EvidenceQuality(StrEnum):
    DIRECT = "direct"
    REPORTED = "reported"
    ESTIMATE = "estimate"


@dataclass(frozen=True)
class AdapterEvidence:
    evidence_id: str
    source: str
    kind: str
    observed_at: datetime
    content: Mapping[str, object]
    quality: EvidenceQuality


class FrigateMetadataAdapter:
    """Consume existing Frigate/MQTT metadata, never raw image meaning."""

    ALLOWED_LABELS = frozenset({"person", "package", "car", "dog", "cat"})

    def normalize(self, payload: Mapping[str, object]) -> AdapterEvidence | None:
        record = payload.get("after")
        values: Mapping[str, object] = (
            cast(Mapping[str, object], record)
            if isinstance(record, Mapping)
            else payload
        )
        event_id = values.get("id")
        label = values.get("label")
        camera = values.get("camera")
        score = values.get("score", values.get("top_score"))
        if not isinstance(event_id, str) or not isinstance(label, str):
            return None
        if label.casefold() not in self.ALLOWED_LABELS or not isinstance(camera, str):
            return None
        confidence = float(score) if isinstance(score, (int, float)) else 0.0
        started = values.get("start_time")
        observed_at = (
            datetime.fromtimestamp(float(started), timezone.utc)
            if isinstance(started, (int, float))
            else datetime.now(timezone.utc)
        )
        content: dict[str, object] = {
            "camera_id": camera[:200],
            "confidence": max(0.0, min(1.0, confidence)),
        }
        zones = values.get("entered_zones", values.get("current_zones"))
        if isinstance(zones, (list, tuple)):
            zone_values = cast(Sequence[object], zones)
            content["zones"] = tuple(
                item[:100] for item in zone_values[:20]
                if isinstance(item, str) and item
            )
        sub_label = values.get("sub_label")
        if isinstance(sub_label, str) and sub_label.strip():
            content["sub_label"] = sub_label.strip()[:100]
        event_type = payload.get("type")
        if isinstance(event_type, str) and event_type in {"new", "update", "end"}:
            content["event_type"] = event_type
        return AdapterEvidence(
            f"frigate:{event_id}",
            "frigate_metadata",
            label.casefold(),
            observed_at,
            content,
            EvidenceQuality.ESTIMATE,
        )


@dataclass(frozen=True)
class DocumentHit:
    document_id: str
    relative_path: str
    title: str
    excerpt: str


class LocalDocumentIndex:
    """Allowlisted local TXT/Markdown index with SQLite FTS5 and path guards."""

    ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".pdf"})

    def __init__(
        self,
        root: str | Path,
        database: str | Path,
        *,
        enabled: bool = False,
        max_file_bytes: int = 2_000_000,
        max_files: int = 1000,
    ) -> None:
        self.root = Path(root).resolve()
        self.database = Path(database)
        self.enabled = enabled
        self.max_file_bytes = max(1024, max_file_bytes)
        self.max_files = max(1, max_files)

    async def async_reindex(self) -> int:
        if not self.enabled:
            return 0
        return await asyncio.to_thread(self._reindex)

    async def async_search(self, query: str, *, limit: int = 5) -> tuple[DocumentHit, ...]:
        if not self.enabled:
            return ()
        cleaned = " ".join(token for token in query.split() if token.isalnum())
        if not cleaned:
            return ()
        return await asyncio.to_thread(self._search, cleaned, max(1, min(20, limit)))

    def _documents(self) -> Iterable[Path]:
        if not self.root.is_dir():
            return ()
        selected: list[Path] = []
        for candidate in sorted(self.root.rglob("*")):
            if len(selected) >= self.max_files:
                break
            resolved = candidate.resolve()
            if self.root not in resolved.parents or not resolved.is_file():
                continue
            if resolved.suffix.casefold() not in self.ALLOWED_SUFFIXES:
                continue
            if resolved.stat().st_size > self.max_file_bytes:
                continue
            selected.append(resolved)
        return tuple(selected)

    def _connect(self) -> sqlite3.Connection:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(document_id UNINDEXED, relative_path UNINDEXED, title, body)"
        )
        return connection

    def _reindex(self) -> int:
        rows: list[tuple[str, str, str, str]] = []
        for path in self._documents():
            raw = path.read_bytes()
            if path.suffix.casefold() == ".pdf":
                text = _extract_pdf_text(raw, max_output=self.max_file_bytes * 4)
                if not text:
                    continue
            else:
                if b"\x00" in raw:
                    continue
                text = raw.decode("utf-8", errors="replace")
            relative = str(path.relative_to(self.root))
            document_id = "doc_" + hashlib.sha256(relative.encode()).hexdigest()[:20]
            title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip()), path.stem)
            rows.append((document_id, relative, title[:300], text))
        with self._connect() as connection:
            connection.execute("DELETE FROM documents")
            connection.executemany("INSERT INTO documents VALUES (?, ?, ?, ?)", rows)
        return len(rows)

    def _search(self, query: str, limit: int) -> tuple[DocumentHit, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document_id, relative_path, title, "
                "snippet(documents, 3, '[', ']', '…', 18) AS excerpt "
                "FROM documents WHERE documents MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            )
            return tuple(
                DocumentHit(
                    str(row["document_id"]),
                    str(row["relative_path"]),
                    str(row["title"]),
                    str(row["excerpt"]),
                )
                for row in rows
            )


class HomeAssistantSourceAdapter:
    """Label already structured HA data without adding interpretations."""

    ALLOWED_SOURCES = frozenset(
        {"calendar", "weather", "recorder", "logbook", "energy", "presence", "todo", "timer"}
    )

    def wrap(
        self,
        source: str,
        stable_id: str,
        content: Mapping[str, object],
        *,
        observed_at: datetime,
    ) -> AdapterEvidence:
        if source not in self.ALLOWED_SOURCES:
            raise ValueError("Nicht freigegebene Home-Assistant-Quelle")
        if observed_at.tzinfo is None:
            raise ValueError("Quellzeit benötigt eine Zeitzone")
        observation_id = int(observed_at.timestamp() * 1_000_000)
        return AdapterEvidence(
            f"ha:{source}:{stable_id}:{observation_id}",
            source,
            "structured_record",
            observed_at,
            dict(content),
            EvidenceQuality.DIRECT,
        )


class StructuredAdapterRuntime:
    """Opt-in HA/Frigate bridge retaining only bounded structured evidence."""

    _DOMAIN_SOURCES = {
        "calendar": "calendar",
        "weather": "weather",
        "person": "presence",
        "device_tracker": "presence",
        "todo": "todo",
        "timer": "timer",
    }
    _SOURCE_ATTRIBUTES = {
        "calendar": ("message", "start_time", "end_time", "location", "all_day"),
        "weather": ("temperature", "humidity", "pressure", "wind_speed"),
        "presence": ("source", "latitude", "longitude", "gps_accuracy"),
        "todo": (),
        "timer": ("remaining", "duration", "finishes_at"),
        "energy": ("last_reset",),
    }

    def __init__(
        self,
        hass: _HassLike,
        entry: _EntryLike,
        sink: Callable[[AdapterEvidence], object],
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._sink = sink
        self._frigate = FrigateMetadataAdapter()
        self._ha_source = HomeAssistantSourceAdapter()
        self._seen_evidence: set[str] = set()

    def _store(self, evidence: AdapterEvidence) -> None:
        """Deduplicate one adapter record before it reaches bounded storage."""
        if evidence.evidence_id in self._seen_evidence:
            return
        self._seen_evidence.add(evidence.evidence_id)
        if len(self._seen_evidence) > 4096:
            self._seen_evidence = {evidence.evidence_id}
        self._sink(evidence)

    async def async_start(self) -> Callable[[], None]:
        @_event_loop_callback
        def _on_state_changed(event: _EventLike) -> None:
            self._handle_state_changed(event)

        @_event_loop_callback
        def _on_frigate_event(event: _EventLike) -> None:
            self._handle_frigate_event(event)

        unsubscribers: list[Callable[[], None]] = []
        bus = getattr(self._hass, "bus", None)
        listen = bus.async_listen if bus is not None else None
        if listen is not None and bool(
            self._entry.options.get(CONF_HA_SOURCES_ENABLED, False)
        ):
            unsubscribers.append(listen("state_changed", _on_state_changed))
        if bool(self._entry.options.get(CONF_FRIGATE_ENABLED, False)):
            if listen is not None:
                unsubscribers.append(listen("frigate_events", _on_frigate_event))
            mqtt_unsubscribe = await self._async_subscribe_mqtt()
            if mqtt_unsubscribe is not None:
                unsubscribers.append(mqtt_unsubscribe)

        def stop() -> None:
            for unsubscribe in reversed(unsubscribers):
                unsubscribe()
            unsubscribers.clear()

        return stop

    def _handle_state_changed(self, event: _EventLike) -> None:
        data = event.data
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not isinstance(entity_id, str) or new_state is None:
            return
        source = self._DOMAIN_SOURCES.get(entity_id.partition(".")[0])
        state = getattr(new_state, "state", None)
        attributes = getattr(new_state, "attributes", {})
        attribute_values: Mapping[str, object] = (
            cast(Mapping[str, object], attributes)
            if isinstance(attributes, Mapping)
            else cast(Mapping[str, object], {})
        )
        if (
            source is None
            and entity_id.startswith("sensor.")
            and attribute_values.get("device_class") in {"energy", "power"}
        ):
            source = "energy"
        if source is None or not isinstance(state, str):
            return
        content: dict[str, object] = {"state": state}
        if attribute_values:
            for key in self._SOURCE_ATTRIBUTES[source]:
                value = attribute_values.get(key)
                if isinstance(value, (str, int, float, bool)):
                    content[key] = value
        observed_at = event.time_fired
        if observed_at.tzinfo is None:
            observed_at = datetime.now(timezone.utc)
        self._store(
            self._ha_source.wrap(
                source, entity_id, content, observed_at=observed_at
            )
        )

    def _handle_frigate_event(self, event: _EventLike) -> None:
        self._store_frigate(event.data)

    def _handle_mqtt_message(self, message: _MessageLike) -> None:
        raw = message.payload
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if len(raw.encode("utf-8")) > 65_536:
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if isinstance(payload, Mapping):
            self._store_frigate(cast(Mapping[str, object], payload))

    def _store_frigate(self, payload: Mapping[str, object]) -> None:
        evidence = self._frigate.normalize(payload)
        if evidence is not None:
            self._store(evidence)

    async def _async_subscribe_mqtt(self) -> Callable[[], None] | None:
        try:
            mqtt = cast(_MqttLike, importlib.import_module("homeassistant.components.mqtt"))
        except ImportError:
            _LOGGER.warning("Frigate MQTT is enabled, but Home Assistant MQTT is unavailable")
            return None
        topic = self._entry.options.get(CONF_FRIGATE_MQTT_TOPIC, "frigate/events")
        try:
            validated_topic = validate_mqtt_topic(topic)
        except ValueError:
            _LOGGER.warning("Frigate MQTT topic is invalid")
            return None
        @_event_loop_callback
        def _on_mqtt_message(message: _MessageLike) -> None:
            self._handle_mqtt_message(message)

        try:
            unsubscribe = await mqtt.async_subscribe(
                self._hass, validated_topic, _on_mqtt_message, qos=0
            )
        except Exception:  # noqa: BLE001 - optional integration boundary
            _LOGGER.warning("Could not subscribe to Frigate MQTT metadata", exc_info=True)
            return None
        return unsubscribe


_PDF_STREAM_RE = re.compile(rb"(?P<dict><<.{0,4096}?>>)[\r\n ]*stream\r?\n(?P<data>.*?)\r?\nendstream", re.S)
_PDF_TEXT_RE = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\)\s*Tj|\[(?P<array>.*?)\]\s*TJ", re.S)
_PDF_ARRAY_STRING_RE = re.compile(rb"\((?P<text>(?:\\.|[^\\)])*)\)", re.S)


def _extract_pdf_text(raw: bytes, *, max_output: int) -> str:
    """Extract literal text operators from bounded, non-encrypted PDFs.

    This deliberately does not OCR images or claim meaning for scanned or
    encrypted documents. Unsupported encodings yield no indexed document.
    """
    if not raw.startswith(b"%PDF-") or b"/Encrypt" in raw:
        return ""
    chunks: list[str] = []
    total = 0
    for match in _PDF_STREAM_RE.finditer(raw):
        data = match.group("data")
        if b"/FlateDecode" in match.group("dict"):
            try:
                data = zlib.decompress(data)
            except zlib.error:
                continue
        if len(data) > max_output:
            continue
        for text_match in _PDF_TEXT_RE.finditer(data):
            literals = (
                (text_match.group("text"),)
                if text_match.group("text") is not None
                else tuple(
                    item.group("text")
                    for item in _PDF_ARRAY_STRING_RE.finditer(
                        text_match.group("array") or b""
                    )
                )
            )
            for literal in literals:
                decoded = _decode_pdf_literal(literal)
                if decoded:
                    chunks.append(decoded)
                    total += len(decoded)
                    if total >= max_output:
                        return " ".join(chunks)[:max_output]
    return " ".join(chunks)


def _decode_pdf_literal(value: bytes) -> str:
    value = re.sub(
        rb"\\([0-7]{1,3})",
        lambda match: bytes((int(match.group(1), 8),)),
        value,
    )
    replacements = {
        rb"\n": b"\n",
        rb"\r": b"\r",
        rb"\t": b"\t",
        rb"\(": b"(",
        rb"\)": b")",
        rb"\\": b"\\",
    }
    for escaped, replacement in replacements.items():
        value = value.replace(escaped, replacement)
    if value.startswith((b"\xfe\xff", b"\xff\xfe")):
        try:
            return value.decode("utf-16").strip()
        except UnicodeDecodeError:
            return ""
    return value.decode("latin-1", errors="ignore").strip()
