"""Scenario runner for the live HomeIntent test bed.

Usage: python runner.py [--only substring] [--category name] [--out results/run.json]

Every scenario starts from a reset house and a fresh conversation. Steps:

- ``say``      sentence (options: ``user``, ``device`` = area name, ``expect``)
- ``set``      drive a simulated sensor: {"set": entity_id, "value": ...}
- ``wait``     seconds
- ``service``  admin service call {"service": "domain.name", "data": {...}}
- ``options``  update HomeIntent options (merged into current options)
- ``check``    expectation block evaluated without speaking
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
HERE = Path(__file__).resolve().parent


def _norm(text: str | None) -> str:
    return (text or "").casefold()


class Runner:
    def __init__(self, session: aiohttp.ClientSession, tokens: dict[str, str]) -> None:
        self.session = session
        self.tokens = tokens
        self.ws: dict[str, WS] = {}
        self.area_devices: dict[str, str] = {}

    async def __aenter__(self):
        for user in ("admin", "anna", "lena"):
            self.ws[user] = await WS(self.session, self.tokens[user]).__aenter__()
        devs = await self.admin.call("config/device_registry/list")
        areas = {a["area_id"]: a["name"] for a in await self.admin.call("config/area_registry/list")}
        for dev in devs:
            if dev.get("area_id") and dev.get("manufacturer") == "Haus-Simulation" and dev.get("model") == "light":
                self.area_devices.setdefault(areas[dev["area_id"]], dev["id"])
        return self

    async def __aexit__(self, *exc):
        for ws in self.ws.values():
            await ws.__aexit__()

    @property
    def admin(self) -> WS:
        return self.ws["admin"]

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
        if "spoken" in expect:
            spoken = [s["message"] for s in log.get("spoken", [])]
            if not any(_norm(expect["spoken"]) in _norm(m) for m in spoken):
                problems.append(f"Keine Sprachausgabe mit '{expect['spoken']}' (erhalten: {spoken})")
        if "played" in expect:
            played = [p["media_id"] for p in log.get("played_media", [])]
            if not any(expect["played"] in m for m in played):
                problems.append(f"Keine Medienausgabe mit '{expect['played']}' (erhalten: {played})")
        if "continue" in expect and bool(turn.get("continue_conversation")) != expect["continue"]:
            problems.append(f"continue_conversation={turn.get('continue_conversation')}")
        return problems

    # ---------------------------------------------------------- scenarios
    async def run(self, scenario: dict[str, Any]) -> dict[str, Any]:
        if not scenario.get("no_reset"):
            await self.service("haus_sim.reset")
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
                    t0 = time.perf_counter()
                    res = await self.ws[user].call("conversation/process", **payload)
                    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    conv_ids[step.get("conv", user)] = res.get("conversation_id")
                    resp = res["response"]
                    rec["speech"] = resp.get("speech", {}).get("plain", {}).get("speech")
                    rec["response_type"] = resp.get("response_type")
                    rec["continue_conversation"] = res.get("continue_conversation")
                    await asyncio.sleep(step.get("settle", 1.0))
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
    args = ap.parse_args()
    chosen = [
        s for s in SCENARIOS
        if (not args.only or args.only in s["id"])
        and (not args.category or s["category"] == args.category)
        and s["category"] not in args.exclude_category
    ]
    results = []
    async with aiohttp.ClientSession() as session:
        async with Runner(session, load_tokens()) as runner:
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
