"""Bridge HomeIntent timer language to Home Assistant's native Assist timers."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .agent_delivery import AgentDelivery
from .const import (
    CONF_AGENT_MEDIA_PLAYERS,
    CONF_AGENT_TTS_ENTITY,
    CONF_TIMER_CHIME_MEDIA_ID,
    DOMAIN,
)
from .productivity import TimerOperation, TimerRequest, format_duration

_LOGGER = logging.getLogger(__name__)


class NativeTimerUnavailableError(ValueError):
    """Raised when no audible native or configured fallback target exists."""


@dataclass(frozen=True)
class NativeTimerInfo:
    """One running or paused Assist timer as reported by Home Assistant."""

    name: str
    seconds_left: int
    is_active: bool
    start_hours: int = 0
    start_minutes: int = 0
    start_seconds: int = 0

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        total = self.start_hours * 3600 + self.start_minutes * 60 + self.start_seconds
        return f"Timer über {format_duration(total)}"

    def target_slots(self) -> dict[str, dict[str, object]]:
        """Slots that make Home Assistant pick exactly this timer."""
        if self.name:
            return {"name": {"value": self.name}}
        slots: dict[str, dict[str, object]] = {}
        for key, value in (
            ("start_hours", self.start_hours),
            ("start_minutes", self.start_minutes),
            ("start_seconds", self.start_seconds),
        ):
            if value:
                slots[key] = {"value": value}
        return slots


def _name_key(name: str) -> str:
    return re.sub(r"[^0-9a-zäöüß]", "", name.casefold())


def match_timer_name(
    spoken: str, timers: Sequence[NativeTimerInfo]
) -> tuple[NativeTimerInfo, ...]:
    """Timers whose name the spoken name refers to.

    Exact matches win. Otherwise one name may extend the other by a short
    inflection ("Nudel" / "Nudeln", "Pizza-Timer" / "Pizza").
    """
    key = _name_key(spoken)
    if not key:
        return ()
    named = [timer for timer in timers if _name_key(timer.name)]
    exact = tuple(timer for timer in named if _name_key(timer.name) == key)
    if exact:
        return exact
    return tuple(
        timer
        for timer in named
        if min(len(key), len(_name_key(timer.name))) >= 3
        and abs(len(key) - len(_name_key(timer.name))) <= 2
        and (
            _name_key(timer.name).startswith(key)
            or key.startswith(_name_key(timer.name))
        )
    )


def describe_timers(timers: Sequence[NativeTimerInfo]) -> str:
    """Spoken list of timers with their remaining time."""
    if not timers:
        return "Es läuft kein Timer."
    parts = [
        f"{timer.label}: {'noch' if timer.is_active else 'pausiert,'} "
        f"{format_duration(timer.seconds_left)}"
        for timer in timers
    ]
    count = "Ein Timer läuft" if len(timers) == 1 else f"{len(timers)} Timer laufen"
    return f"{count}. " + "; ".join(parts) + "."


def join_timer_labels(timers: Sequence[NativeTimerInfo]) -> str:
    labels = [timer.label for timer in timers]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " oder " + labels[-1]


_JOURNAL_KEY = "homeintent_timer_journal"
_JOURNAL_LIMIT = 32


@dataclass(frozen=True)
class LostTimer:
    """A timer that was running when Home Assistant stopped (F19)."""

    label: str
    ends_at: datetime


def restart_note(lost: Sequence[LostTimer], now: datetime) -> str | None:
    """Spoken hint about timers lost to a Home Assistant restart."""
    if not lost:
        return None
    parts: list[str] = []
    for timer in lost[:3]:
        local_end = dt_util.as_local(timer.ends_at)
        when = "ist inzwischen abgelaufen" if timer.ends_at <= now else f"wäre um {local_end:%H:%M} Uhr abgelaufen"
        parts.append(f"„{timer.label}“ ({when})")
    noun = "der Timer" if len(lost) == 1 else "die Timer"
    return (
        f"Hinweis: Durch den Neustart von Home Assistant ist {noun} "
        f"{', '.join(parts)} verloren gegangen. Bitte stelle ihn bei Bedarf neu."
        if len(lost) == 1 else
        f"Hinweis: Durch den Neustart von Home Assistant sind {noun} "
        f"{', '.join(parts)} verloren gegangen. Bitte stelle sie bei Bedarf neu."
    )


class NativeTimerRuntime:
    """Use HA's TimerManager; HomeIntent never runs a parallel scheduler.

    Assist timers live only in memory. A small journal of started timers
    (label and end time) lets HomeIntent tell the user after a restart which
    timers were lost instead of silently answering "Es läuft kein Timer" (F19).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._fallback_device_id = f"{DOMAIN}:{entry.entry_id}"
        self._delivery = AgentDelivery(hass)
        self._unregister: Callable[[], None] | None = None
        self._journal: list[dict[str, str]] = []
        self._lost: list[LostTimer] = []
        self._store: object | None = None

    async def async_load_journal(self) -> None:
        """Timers journaled before this start did not survive it."""
        try:
            from homeassistant.helpers.storage import Store

            store: Store[dict[str, list[dict[str, str]]]] = Store(self._hass, 1, _JOURNAL_KEY)
            self._store = store
            raw = await store.async_load()
        except Exception:  # noqa: BLE001 - the hint is best effort
            _LOGGER.debug("HomeIntent timer journal unavailable", exc_info=True)
            return
        entries = raw.get("timers", []) if isinstance(raw, dict) else []
        for item in entries[-_JOURNAL_LIMIT:]:
            try:
                ends_at = datetime.fromisoformat(str(item["ends_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            self._lost.append(LostTimer(str(item.get("label") or "Timer"), ends_at))
        self._journal = []
        await self._async_save_journal()

    def consume_restart_note(self) -> str | None:
        """The restart hint, spoken once with the next timer status answer."""
        note = restart_note(self._lost, dt_util.utcnow())
        self._lost = []
        return note

    async def _async_save_journal(self) -> None:
        store = self._store
        if store is None:
            return
        try:
            await store.async_save({"timers": self._journal[-_JOURNAL_LIMIT:]})  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - never affect the timer itself
            _LOGGER.debug("HomeIntent timer journal not saved", exc_info=True)

    async def _async_journal_start(self, label: str, seconds: int) -> None:
        ends_at = dt_util.utcnow() + timedelta(seconds=seconds)
        self._journal.append({"label": label, "ends_at": ends_at.isoformat()})
        await self._async_save_journal()

    async def _async_journal_remove(self, label: str | None) -> None:
        now = dt_util.utcnow()
        kept = [
            item for item in self._journal
            if datetime.fromisoformat(item["ends_at"]) > now
        ]
        if label is None:
            kept = []
        else:
            for index, item in enumerate(kept):
                if _name_key(item["label"]) == _name_key(label):
                    del kept[index]
                    break
        if kept != self._journal:
            self._journal = kept
            await self._async_save_journal()

    def async_start(self) -> Callable[[], None]:
        """Register one synthetic timer device for configured TTS fallback."""
        try:
            from homeassistant.components.intent import async_register_timer_handler

            self._unregister = async_register_timer_handler(
                self._hass, self._fallback_device_id, self._handle_timer_event
            )
        except (ImportError, KeyError):
            _LOGGER.warning("Home Assistant's native Assist timer manager is unavailable")

        def stop() -> None:
            if self._unregister is not None:
                self._unregister()
                self._unregister = None

        return stop

    def _has_tts_fallback(self) -> bool:
        options: Mapping[str, object] = self._entry.options
        tts = options.get(CONF_AGENT_TTS_ENTITY)
        players = options.get(CONF_AGENT_MEDIA_PLAYERS)
        return (
            isinstance(tts, str)
            and bool(tts)
            and isinstance(players, (list, tuple))
            and any(isinstance(player, str) and player for player in players)
        )

    def _handle_timer_event(self, event_type: object, timer: object) -> None:
        if str(event_type) != "finished":
            return
        name = getattr(timer, "name", None)
        message = (
            f"Der Timer {name.strip()} ist abgelaufen."
            if isinstance(name, str) and name.strip()
            else "Der Timer ist abgelaufen."
        )
        self._hass.async_create_task(
            self._async_announce(message),
            name="HomeIntent timer announcement",
        )
        self._hass.async_create_task(
            self._async_journal_remove(name.strip() if isinstance(name, str) and name.strip() else ""),
            name="HomeIntent timer journal",
        )
        # V12 may observe the expiry for history only; NativeTimer remains the
        # sole announcer, so one expiry yields exactly one announcement.
        runtime_data = getattr(self._entry, "runtime_data", None)
        proactive = getattr(runtime_data, "proactive_context", None)
        if proactive is not None:
            try:
                proactive.record_timer_finished(
                    name.strip() if isinstance(name, str) and name.strip() else "Timer"
                )
            except Exception:  # noqa: BLE001 - history must never affect the timer
                _LOGGER.debug("V12 timer history note failed", exc_info=True)

    async def _async_announce(self, message: str) -> None:
        # The name comes first so a listener knows which timer ended; the
        # configured alert tone follows as the actual alarm.
        try:
            await self._delivery.async_speak(message, self._entry.options)
        except Exception as err:  # noqa: BLE001 - HA service errors vary by provider
            _LOGGER.error("HomeIntent timer announcement failed: %s", err)
        await self._async_play_chime()

    async def _async_play_chime(self) -> None:
        """Play an explicitly configured local/HA media-source cue.

        The cue is optional and bounded to Home Assistant's own media-source
        identifiers; HomeIntent never fetches an arbitrary URL.  A cue
        failure must not suppress the spoken timer information.
        """
        media_id = self._entry.options.get(CONF_TIMER_CHIME_MEDIA_ID)
        raw_players = self._entry.options.get(CONF_AGENT_MEDIA_PLAYERS, ())
        players = (
            [str(item) for item in raw_players if isinstance(item, str) and item]
            if isinstance(raw_players, (list, tuple))
            else []
        )
        if not isinstance(media_id, str) or not media_id.strip() or not players:
            return
        media_id = media_id.strip()
        if not media_id.startswith("media-source://"):
            _LOGGER.warning("Ignoring non-local timer chime media identifier")
            return
        try:
            await self._hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": players,
                    "media_content_id": media_id,
                    "media_content_type": "music",
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("HomeIntent timer chime failed: %s", err)

    async def _route_device(self, source_device_id: str | None) -> str:
        from homeassistant.components.intent import async_device_supports_timers

        if source_device_id and async_device_supports_timers(
            self._hass, source_device_id
        ):
            return source_device_id
        if self._unregister is not None and self._has_tts_fallback():
            return self._fallback_device_id
        raise NativeTimerUnavailableError(
            "Dieser Assist-Client kann keinen hörbaren Timer ausgeben. "
            "Konfiguriere in HomeIntent eine lokale TTS-Engine und mindestens "
            "einen Medienplayer."
        )

    async def async_ensure_audible(self, user_input: object) -> None:
        """Raise when a new timer could not be heard when it finishes."""
        await self._route_device(getattr(user_input, "device_id", None))

    def _command_device(self, source_device_id: str | None) -> str | None:
        """Device for commands on existing timers; no audible output needed."""
        from homeassistant.components.intent import async_device_supports_timers

        if source_device_id and async_device_supports_timers(
            self._hass, source_device_id
        ):
            return source_device_id
        return self._fallback_device_id if self._unregister is not None else None

    async def _async_handle(
        self,
        intent_type: str,
        slots: dict[str, dict[str, object]],
        user_input: object,
        device_id: str | None,
    ) -> object:
        from homeassistant.helpers import intent

        return await intent.async_handle(
            self._hass,
            DOMAIN,
            intent_type,
            slots,
            getattr(user_input, "text", None),
            getattr(user_input, "context", None),
            language=getattr(user_input, "language", "de"),
            assistant="conversation",
            device_id=device_id,
            satellite_id=getattr(user_input, "satellite_id", None),
            conversation_agent_id=getattr(user_input, "agent_id", None),
        )

    async def async_list_timers(self, user_input: object) -> tuple[NativeTimerInfo, ...]:
        """All Assist timers, nearest to the asking device first."""
        from homeassistant.helpers import intent

        native_response = await self._async_handle(
            intent.INTENT_TIMER_STATUS,
            {},
            user_input,
            self._command_device(getattr(user_input, "device_id", None)),
        )
        speech_slots = getattr(native_response, "speech_slots", {})
        raw_timers: object = (
            speech_slots.get("timers") if isinstance(speech_slots, dict) else None
        )
        timers: list[NativeTimerInfo] = []
        if isinstance(raw_timers, list):
            for item in raw_timers:
                if not isinstance(item, dict):
                    continue
                seconds = item.get("total_seconds_left")
                if not isinstance(seconds, int):
                    continue
                name = item.get("name")
                timers.append(
                    NativeTimerInfo(
                        name=name.strip() if isinstance(name, str) else "",
                        seconds_left=seconds,
                        is_active=bool(item.get("is_active", True)),
                        start_hours=_int_slot(item.get("start_hours")),
                        start_minutes=_int_slot(item.get("start_minutes")),
                        start_seconds=_int_slot(item.get("start_seconds")),
                    )
                )
        return tuple(timers)

    async def async_cancel_all(self, user_input: object) -> int:
        from homeassistant.helpers import intent

        native_response = await self._async_handle(
            intent.INTENT_CANCEL_ALL_TIMERS,
            {},
            user_input,
            self._command_device(getattr(user_input, "device_id", None)),
        )
        speech_slots = getattr(native_response, "speech_slots", {})
        canceled = speech_slots.get("canceled") if isinstance(speech_slots, dict) else None
        await self._async_journal_remove(None)
        return canceled if isinstance(canceled, int) else 0

    @staticmethod
    def _duration_slots(seconds: int) -> dict[str, dict[str, object]]:
        hours, remainder = divmod(abs(seconds), 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        values = {"hours": hours, "minutes": minutes, "seconds": remaining_seconds}
        slots: dict[str, dict[str, object]] = {
            key: {"value": value}
            for key, value in values.items()
            if value
        }
        if not slots:
            slots["seconds"] = {"value": 0}
        return slots

    async def async_execute(
        self,
        request: TimerRequest,
        user_input: object,
        *,
        target: NativeTimerInfo | None = None,
    ) -> str:
        """Execute a parsed request through HA's registered timer intents.

        ``target`` is the concrete timer HomeIntent resolved beforehand; it
        replaces the spoken name so Home Assistant's exact name match applies.
        """
        from homeassistant.helpers import intent

        operation = request.operation
        if operation is TimerOperation.START:
            device_id = await self._route_device(getattr(user_input, "device_id", None))
        else:
            device_id = self._command_device(getattr(user_input, "device_id", None))
        if operation is TimerOperation.START:
            intent_type = intent.INTENT_START_TIMER
        elif operation is TimerOperation.CHANGE:
            intent_type = (
                intent.INTENT_INCREASE_TIMER
                if (request.change_seconds or 0) >= 0
                else intent.INTENT_DECREASE_TIMER
            )
        elif operation is TimerOperation.PAUSE:
            intent_type = intent.INTENT_PAUSE_TIMER
        elif operation is TimerOperation.RESUME:
            intent_type = intent.INTENT_UNPAUSE_TIMER
        elif operation in {TimerOperation.CANCEL, TimerOperation.FINISH}:
            intent_type = intent.INTENT_CANCEL_TIMER
        else:
            intent_type = intent.INTENT_TIMER_STATUS
        slots: dict[str, dict[str, object]] = {}
        seconds = int(
            (request.duration_seconds or 0)
            if operation is TimerOperation.START
            else abs(request.change_seconds or 0)
        )
        if operation in {TimerOperation.START, TimerOperation.CHANGE}:
            if not seconds:
                raise ValueError("Für den Timer fehlt eine gültige Dauer.")
            slots.update(self._duration_slots(seconds))
        name = request.name
        if target is not None and operation is not TimerOperation.START:
            slots.update(target.target_slots())
            name = target.name or None
        elif request.name:
            slots["name"] = {"value": request.name}
        native_response = await self._async_handle(intent_type, slots, user_input, device_id)
        label = f" „{name}“" if name else ""
        if operation is TimerOperation.START:
            await self._async_journal_start(name or f"Timer über {format_duration(seconds)}", seconds)
            return f"Timer{label} für {format_duration(seconds)} gestartet."
        if operation in {TimerOperation.CANCEL, TimerOperation.FINISH}:
            await self._async_journal_remove(name or (target.label if target is not None else None))
        if operation is TimerOperation.CHANGE:
            verb = "verlängert" if (request.change_seconds or 0) > 0 else "verkürzt"
            return f"Timer{label} um {format_duration(seconds)} {verb}."
        return {
            TimerOperation.PAUSE: f"Timer{label} pausiert.",
            TimerOperation.RESUME: f"Timer{label} fortgesetzt.",
            TimerOperation.CANCEL: f"Timer{label} abgebrochen.",
            TimerOperation.FINISH: f"Timer{label} beendet.",
        }.get(operation) or self._status_speech(native_response)

    @staticmethod
    def _status_speech(native_response: object) -> str:
        speech_slots = getattr(native_response, "speech_slots", {})
        timers = speech_slots.get("timers") if isinstance(speech_slots, dict) else None
        if not isinstance(timers, list) or not timers:
            return "Es läuft kein passender Timer."
        rendered: list[str] = []
        for item in timers[:3]:
            if not isinstance(item, dict):
                continue
            seconds = item.get("total_seconds_left")
            if not isinstance(seconds, int):
                continue
            name = item.get("name")
            prefix = f"{name}: " if isinstance(name, str) and name else ""
            state = "verbleiben" if item.get("is_active", True) else "pausiert, verbleibend"
            rendered.append(f"{prefix}{state} {format_duration(seconds)}")
        if not rendered:
            return "Der Timerstatus ist derzeit nicht verfügbar."
        suffix = " Weitere Timer nenne ich auf Nachfrage." if len(timers) > 3 else ""
        return "; ".join(rendered) + "." + suffix


def _int_slot(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0
