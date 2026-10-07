"""Scenario runner for the live HomeIntent test bed.

Usage: python runner.py [--only substring] [--category name] [--out results/run.json]

Every scenario starts from a reset house and a fresh conversation. Steps:

- ``say``      sentence (options: ``user``, ``device`` = area name, ``household`` =
               satellite turn without a signed-in user, ``expect``)
- ``set``      drive a simulated sensor: {"set": entity_id, "value": ...}
- ``wait``     seconds
- ``service``  admin service call {"service": "domain.name", "data": {...}}
- ``options``  update HomeIntent options (merged into current options)
- ``check``    expectation block evaluated without speaking
- ``pipeline`` sentence through the real Assist pipeline (text in, TTS end)
  on the simulated satellite's device; records whether TTS was produced

``say`` with ``satellite: True`` speaks as the simulated voice satellite
(``assist_satellite.kuechen_satellit``); its announcements are logged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
from typing import Any

import aiohttp

from haclient import WS, HAError, load_tokens, rest

AGENT = "conversation.homeintent"
SATELLITE = "assist_satellite.kuechen_satellit"
HERE = Path(__file__).resolve().parent


def _norm(text: str | None) -> str:
    return (text or "").casefold()


class Runner:
    def __init__(self, session: aiohttp.ClientSession, tokens: dict[str, str]) -> None:
        self.session = session
        self.tokens = tokens
        self.ws: dict[str, WS] = {}
        self.area_devices: dict[str, str] = {}
        self.full_reset = False
        self.satellite_device: str | None = None
        self.pipeline_id: str | None = None

    async def __aenter__(self):
        for user in ("admin", "anna", "lena"):
            self.ws[user] = await WS(self.session, self.tokens[user]).__aenter__()
        devs = await self.admin.call("config/device_registry/list")
        areas = {a["area_id"]: a["name"] for a in await self.admin.call("config/area_registry/list")}
        for dev in devs:
            if dev.get("area_id") and dev.get("manufacturer") == "Haus-Simulation" and dev.get("model") == "light":
                self.area_devices.setdefault(areas[dev["area_id"]], dev["id"])
            if dev.get("manufacturer") == "Haus-Simulation" and dev.get("model") == "assist_satellite":
                self.satellite_device = dev["id"]
        return self

    async def ensure_pipeline(self) -> str:
        """An Assist pipeline with HomeIntent and the simulated TTS (7.9.1 B)."""
        if self.pipeline_id is not None:
            return self.pipeline_id
        listed = await self.admin.call("assist_pipeline/pipeline/list")
        for item in listed["pipelines"]:
            if item["name"] == "HomeIntent Testbett":
                self.pipeline_id = item["id"]
                return item["id"]
        created = await self.admin.call(
            "assist_pipeline/pipeline/create", conversation_engine=AGENT, conversation_language="de",
            language="de", name="HomeIntent Testbett", stt_engine=None, stt_language=None,
            tts_engine="tts.sprachausgabe", tts_language="de", tts_voice=None,
            wake_word_entity=None, wake_word_id=None,
        )
        self.pipeline_id = created["id"]
        return created["id"]

    async def __aexit__(self, *exc):
        for ws in self.ws.values():
            await ws.__aexit__()

    @property
    def admin(self) -> WS:
        return self.ws["admin"]

    async def effect_wait_statistics(self) -> dict[str, Any] | None:
        try:
            entries = await rest(self.session, "GET", "/api/config/config_entries/entry", self.tokens["admin"])
            entry = next(e for e in entries if e["domain"] == "homeintent")
            diag = await rest(
                self.session, "GET", f"/api/diagnostics/config_entry/{entry['entry_id']}", self.tokens["admin"]
            )
        except Exception:  # noqa: BLE001 - statistics are informative only
            return None
        return (diag.get("data") or {}).get("effect_wait")

    async def service(self, name: str, data: dict | None = None, response: bool = False) -> Any:
        domain, service = name.split(".", 1)
        payload: dict[str, Any] = {"domain": domain, "service": service, "service_data": data or {}}
        if response:
            payload["return_response"] = True
        res = await self.admin.call("call_service", **payload)
        return res.get("response") if response else res

    async def sim_log(self) -> dict[str, list]:
        return await self.service("haus_sim.get_log", response=True)

    async def states(self) -> dict[str, dict]:
        return {s["entity_id"]: s for s in await self.admin.call("get_states")}

    async def set_options(self, changes: dict[str, Any]) -> None:
        entries = await rest(self.session, "GET", "/api/config/config_entries/entry", self.tokens["admin"])
        entry = next(e for e in entries if e["domain"] == "homeintent")
        flow = await rest(self.session, "POST", "/api/config/config_entries/options/flow", self.tokens["admin"], json={"handler": entry["entry_id"]})
        defaults = {}
        for field in flow["data_schema"]:
            desc = field.get("description") or {}
            if "suggested_value" in desc:
                defaults[field["name"]] = desc["suggested_value"]
            elif "default" in field:
                defaults[field["name"]] = field["default"]
        data = {k: v for k, v in defaults.items() if v is not None}
        data.update(changes)
        res = await rest(self.session, "POST", f"/api/config/config_entries/options/flow/{flow['flow_id']}", self.tokens["admin"], json=data)
        if res.get("type") != "create_entry":
            raise HAError(f"options rejected: {res}")
        await asyncio.sleep(3)  # entry reload

    def subst(self, value: Any) -> Any:
        """Replace "$admin_user_id"-style placeholders with bootstrap values."""
        if isinstance(value, str) and value.startswith("$"):
            return self.tokens[value[1:]]
        if isinstance(value, list):
            return [self.subst(v) for v in value]
        if isinstance(value, dict):
            return {k: self.subst(v) for k, v in value.items()}
        return value

    # ------------------------------------------------------------ checks
    def evaluate(self, expect: dict[str, Any], turn: dict[str, Any], states: dict[str, dict], log: dict[str, list]) -> list[str]:
        problems: list[str] = []
        speech = _norm(turn.get("speech"))
        if "type" in expect:
            wanted = expect["type"] if isinstance(expect["type"], list) else [expect["type"]]
            if turn.get("response_type") not in wanted:
                problems.append(f"Antworttyp {turn.get('response_type')} statt {wanted}")
        if "any" in expect and not any(_norm(s) in speech for s in expect["any"]):
            problems.append(f"Antwort enthält keins von {expect['any']}")
        for s in expect.get("all", []):
            if _norm(s) not in speech:
                problems.append(f"Antwort enthält nicht '{s}'")
        for s in expect.get("none", []):
            if _norm(s) in speech:
                problems.append(f"Antwort enthält unerwartet '{s}'")
        calls = [f"{c['entity_id']}:{c['action']}" for c in log.get("calls", [])]
        for c in expect.get("calls", []):
            if c not in calls:
                problems.append(f"Aufruf fehlt: {c}")
        if expect.get("only_calls") and set(calls) - set(expect.get("calls", [])):
            problems.append(f"Unerwartete Aufrufe: {sorted(set(calls) - set(expect.get('calls', [])))}")
        if expect.get("no_calls") and calls:
            problems.append(f"Unerwartete Aufrufe: {calls}")
        for c in expect.get("not_calls", []):
            if c in calls:
                problems.append(f"Verbotener Aufruf: {c}")
        for entity_id, want in expect.get("state", {}).items():
            st = states.get(entity_id)
            if st is None:
                problems.append(f"{entity_id} existiert nicht")
                continue
            if isinstance(want, dict):
                for attr, val in want.items():
                    have = st["state"] if attr == "state" else st["attributes"].get(attr)
                    if isinstance(val, (int, float)) and isinstance(have, (int, float)):
                        if not math.isclose(have, val, abs_tol=1.01):
                            problems.append(f"{entity_id}.{attr}={have} statt {val}")
                    elif have != val:
                        problems.append(f"{entity_id}.{attr}={have!r} statt {val!r}")
            elif st["state"] != want:
                problems.append(f"{entity_id}={st['state']} statt {want}")
        if "notify" in expect:
            msgs = [n["message"] for n in log.get("notifications", [])]
            if expect["notify"] is False:
                if msgs:
                    problems.append(f"Unerwartete Push-Nachricht: {msgs}")
            elif not any(_norm(expect["notify"]) in _norm(m) for m in msgs):
                problems.append(f"Keine Push-Nachricht mit '{expect['notify']}' (erhalten: {msgs})")
        if "notify_count" in expect:
            # Wirkung statt Vorschau (7.8.3): genau so viele Push-Nachrichten
            # seit dem letzten Log-Löschen, bei Bedarf nur an ein Ziel.
            sent = [
                n for n in log.get("notifications", [])
                if ("notify_to" not in expect or n.get("target") == expect["notify_to"])
                and ("notify_match" not in expect or _norm(expect["notify_match"]) in _norm(n.get("message")))
                and ("notify_exact" not in expect or n.get("message") == expect["notify_exact"])
            ]
            if len(sent) != expect["notify_count"]:
                problems.append(
                    f"{len(sent)} Push-Nachrichten statt {expect['notify_count']} "
                    f"(erhalten: {[(n.get('target'), n.get('message')) for n in log.get('notifications', [])]})"
                )
        if "spoken" in expect:
            spoken = [s["message"] for s in log.get("spoken", [])]
            if not any(_norm(expect["spoken"]) in _norm(m) for m in spoken):
                problems.append(f"Keine Sprachausgabe mit '{expect['spoken']}' (erhalten: {spoken})")
        if "played" in expect:
            played = [p["media_id"] for p in log.get("played_media", [])]
            if not any(expect["played"] in m for m in played):
                problems.append(f"Keine Medienausgabe mit '{expect['played']}' (erhalten: {played})")
        if "announce_count" in expect:
            # Bestätigungston (7.9.1 B): genau so viele Ansagen auf dem Satelliten.
            tones = [
                a for a in log.get("announcements", [])
                if "announce_match" not in expect or expect["announce_match"] in (a.get("media_id") or a.get("message") or "")
            ]
            if len(tones) != expect["announce_count"]:
                problems.append(f"{len(tones)} Ansagen statt {expect['announce_count']} (erhalten: {log.get('announcements')})")
        if "speech_empty" in expect and (not (turn.get("speech") or "").strip()) != expect["speech_empty"]:
            problems.append(f"Sprachausgabe {'nicht ' if expect['speech_empty'] else ''}leer: {turn.get('speech')!r}")
        if "tts" in expect and bool(turn.get("tts")) != expect["tts"]:
            problems.append(f"TTS {'nicht ' if expect['tts'] else ''}erzeugt: {turn.get('pipeline_events')}")
        if "continue" in expect and bool(turn.get("continue_conversation")) != expect["continue"]:
            problems.append(f"continue_conversation={turn.get('continue_conversation')}")
        return problems

    # ---------------------------------------------------------- scenarios
    async def run(self, scenario: dict[str, Any]) -> dict[str, Any]:
        if not scenario.get("no_reset"):
            await self.service("haus_sim.reset", {"full": True} if self.full_reset else None)
        conv_ids: dict[str, str | None] = {}
        out_steps = []
        for step in scenario["steps"]:
            rec: dict[str, Any] = {"step": {k: v for k, v in step.items() if k != "expect"}}
            try:
                if "say" in step:
                    user = step.get("user", "admin")
                    await self.service("haus_sim.clear_log")
                    payload = {"text": step["say"], "agent_id": AGENT, "language": "de", "conversation_id": conv_ids.get(step.get("conv", user))}
                    if step.get("device"):
                        payload["device_id"] = self.area_devices[step["device"]]
                    if step.get("satellite"):
                        payload["satellite_id"] = SATELLITE
                        payload["device_id"] = self.satellite_device
                    t0 = time.perf_counter()
                    if step.get("household"):
                        # Satellite turn without a signed-in user (7.9.2 A3).
                        user = step.get("conv", "household")
                        voice = await self.service("haus_sim.voice", {
                            "text": step["say"], "agent_id": AGENT, "conversation_id": conv_ids.get(user),
                        }, response=True)
                        res = {
                            "conversation_id": voice.get("conversation_id"),
                            "continue_conversation": voice.get("continue_conversation"),
                            "response": {
                                "speech": {"plain": {"speech": voice.get("speech")}},
                                "response_type": voice.get("response_type"),
                            },
                        }
                    else:
                        res = await self.ws[user].call("conversation/process", **payload)
                    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    conv_ids[step.get("conv", user)] = res.get("conversation_id")
                    resp = res["response"]
                    rec["speech"] = resp.get("speech", {}).get("plain", {}).get("speech")
                    rec["response_type"] = resp.get("response_type")
                    rec["continue_conversation"] = res.get("continue_conversation")
                    await asyncio.sleep(step.get("settle", 1.0))
                elif "pipeline" in step:
                    await self.service("haus_sim.clear_log")
                    events = await self.admin.run_pipeline(
                        step["pipeline"], end_stage="tts", pipeline=await self.ensure_pipeline(),
                        device_id=self.satellite_device,
                        conversation_id=conv_ids.get(step.get("conv", "admin")),
                    )
                    kinds = [event["type"] for event in events]
                    rec["pipeline_events"] = kinds
                    rec["tts"] = "tts-start" in kinds
                    intent_end = next((e for e in events if e["type"] == "intent-end"), None)
                    response = ((intent_end or {}).get("data") or {}).get("intent_output", {}).get("response", {})
                    rec["speech"] = response.get("speech", {}).get("plain", {}).get("speech")
                    rec["response_type"] = response.get("response_type")
                    await asyncio.sleep(step.get("settle", 1.5))
                elif "set" in step:
                    await self.service("haus_sim.set", {"entity_id": step["set"], "value": step["value"]})
                    await asyncio.sleep(step.get("settle", 0.5))
                elif "wait" in step:
                    await asyncio.sleep(step["wait"])
                elif "service" in step:
                    rec["response"] = await self.service(step["service"], self.subst(step.get("data")), response=step.get("response", False))
                elif "options" in step:
                    await self.set_options(self.subst(step["options"]))
                if "expect" in step:
                    log = await self.sim_log()
                    rec["calls"] = [f"{c['entity_id']}:{c['action']} {json.dumps(c['data'], ensure_ascii=False) if c['data'] else ''}".strip() for c in log["calls"]]
                    rec["notifications"] = log["notifications"]
                    rec["spoken"] = log["spoken"]
                    rec["played_media"] = log["played_media"]
                    rec["announcements"] = log.get("announcements", [])
                    rec["problems"] = self.evaluate(step["expect"], rec, await self.states(), log)
            except Exception as err:  # noqa: BLE001 - a crash is a finding
                rec["problems"] = [f"Ausnahme: {type(err).__name__}: {err}"]
            out_steps.append(rec)
        failed = sum(1 for r in out_steps if r.get("problems"))
        return {"id": scenario["id"], "category": scenario["category"], "title": scenario["title"], "steps": out_steps, "passed": failed == 0}


async def main() -> None:
    from scenarios import SCENARIOS

    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--category")
    ap.add_argument("--exclude-category", action="append", default=[],
                    help="Kategorie überspringen (mehrfach möglich), z. B. die langen Proaktiv-Szenarien")
    ap.add_argument("--strict", action="store_true",
                    help="Exit-Code 1, sobald ein Szenario fehlschlägt (CI)")
    ap.add_argument("--out", default=str(HERE / "results" / "run.json"))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--full-reset", action="store_true",
                    help="also clear lists, timers and test automations before every scenario (R11)")
    args = ap.parse_args()
    chosen = [
        s for s in SCENARIOS
        if (not args.only or args.only in s["id"])
        and (not args.category or s["category"] == args.category)
        and s["category"] not in args.exclude_category
    ]
    results = []
    effect_wait: dict[str, Any] | None = None
    async with aiohttp.ClientSession() as session:
        async with Runner(session, load_tokens()) as runner:
            runner.full_reset = args.full_reset
            for sc in chosen:
                res = await runner.run(sc)
                results.append(res)
                mark = "PASS" if res["passed"] else "FAIL"
                print(f"[{mark}] {sc['id']}: {sc['title']}")
                for st in res["steps"]:
                    if "say" in st["step"] and (args.verbose or st.get("problems")):
                        print(f"     > {st['step']['say']}\n     < [{st.get('response_type')}] {st.get('speech')}")
                        for c in st.get("calls", []):
                            print(f"         call {c}")
                    for p in st.get("problems") or []:
                        print(f"       ✗ {p}")
            effect_wait = await runner.effect_wait_statistics()
    if effect_wait is not None:
        # 7.9.2 A1: extra wait for device reports, measured by HomeIntent
        # itself (not part of the language-understanding latency budget).
        print(f"Zusätzliche Wartezeit auf Geräte-Rückmeldung: {json.dumps(effect_wait, ensure_ascii=False)}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text()) if out.exists() else {}
    for res in results:
        existing[res["id"]] = res
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=1))
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} Szenarien bestanden")
    if args.strict and not all(r["passed"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
