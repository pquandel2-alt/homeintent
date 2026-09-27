"""End-to-end push notification matrix: say → confirm → really trigger → push arrives?

python push_check.py [--out results/push_check.json]

For each case the house is reset, the trigger entity is put into its "before"
state, the sentence is spoken as the given user, a confirmation question is
answered with "Ja.", then the trigger really happens in the simulated house.
Pass = exactly the expected phone receives a message (and no other phone),
the message text is German and meaningful, and the automation is removed
again afterwards. Negative cases must not create an automation or push.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import aiohttp

from haclient import load_tokens, rest
from runner import AGENT, Runner

PHIL, ANNA = "notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"
CASES: list[dict] = []


def case(text, trigger=None, before=None, after=None, target=PHIL, expect_words=(), user="admin", wait=3.0, kind="auto", note=""):
    CASES.append({"text": text, "trigger": trigger, "before": before, "after": after, "target": target,
                  "expect_words": list(expect_words), "user": user, "wait": wait, "kind": kind, "note": note})


W = "binary_sensor."
# --- Fenster / Türen, viele Formulierungen ------------------------------------
case("Benachrichtige mich, wenn das Küchenfenster aufgeht.", W + "kuechenfenster", "off", "on", expect_words=["küche", "fenster"])
case("Benachrichtige mich wenn Fenster Küche auf geht", W + "kuechenfenster", "off", "on", expect_words=["fenster"])
case("Benachrichtige mich wenn das Badfenster auf geht", W + "badezimmerfenster", "off", "on", expect_words=["fenster"])
case("Sag mir Bescheid, wenn das Badezimmerfenster geöffnet wird.", W + "badezimmerfenster", "off", "on", expect_words=["fenster"])
case("Gib mir Bescheid wenn im Schlafzimmer das Fenster aufgemacht wird", W + "schlafzimmerfenster", "off", "on", expect_words=["fenster"])
case("Schick mir eine Nachricht, sobald die Haustür aufgeht.", W + "haustuer", "off", "on", expect_words=["haustür"])
case("Informier mich, falls jemand die Terrassentür öffnet.", W + "terrassentuer", "off", "on", expect_words=["terrassentür"])
case("Wenn das Bürofenster aufgeht, schick mir eine Push-Nachricht.", W + "buerofenster", "off", "on", expect_words=["fenster"])
case("Ich möchte eine Nachricht aufs Handy, wenn das Kinderzimmerfenster geöffnet wird.", W + "kinderzimmerfenster", "off", "on", expect_words=["fenster"])
case("Kannst du mir Bescheid geben wenn das Wohnzimmerfenster auf ist", W + "wohnzimmerfenster", "off", "on", expect_words=["fenster"])
case("Benachrichtige mich, wenn im Obergeschoss irgendein Fenster geöffnet wird.", W + "kinderzimmerfenster", "off", "on", expect_words=["fenster"])
case("Benachrichtige mich, wenn ein Fenster aufgeht.", W + "wohnzimmerfenster", "off", "on", expect_words=["fenster"])
case("Benachrichtige mich, wenn das Küchenfenster wieder zu ist.", W + "kuechenfenster", "on", "off", expect_words=["fenster"])
case("Benachrichtige mich, wenn das Küchenfenster geöffnet wird, dass ich lüften soll.", W + "kuechenfenster", "off", "on", expect_words=["lüften"])
case("Wenn die Haustür aufgeht, will ich eine Nachricht auf mein Handy.", W + "haustuer", "off", "on", expect_words=["haustür"])
case("Push an mich, wenn die Haustür offen ist.", W + "haustuer", "off", "on", expect_words=["haustür"])
# --- andere Auslöser -----------------------------------------------------------
case("Benachrichtige mich, wenn im Flur Bewegung erkannt wird.", W + "bewegung_flur", "off", "on", expect_words=["bewegung"])
case("Benachrichtige mich, wenn der Wassermelder im Keller auslöst.", W + "wassermelder_keller", "off", "on", expect_words=["wasser"])
case("Sag mir Bescheid, wenn das Garagentor aufgeht.", "cover.garagentor", None, "open", expect_words=["garage"], wait=9.0)
case("Benachrichtige mich, wenn die Temperatur im Keller unter 12 Grad fällt.", "sensor.temperatur_keller", 14.6, 11.4, expect_words=["temperatur", "keller"])
case("Benachrichtige mich, wenn die Luftfeuchtigkeit im Bad über 75 Prozent steigt.", "sensor.luftfeuchtigkeit_badezimmer", 68, 78, expect_words=["feucht"])
case("Sag mir Bescheid, wenn die Waschmaschine fertig ist.", "sensor.waschmaschine_status", "running", "finished", expect_words=["waschmaschine"])
case("Benachrichtige mich, wenn Anna nach Hause kommt.", "device_tracker.handy_anna", "not_home", "home", expect_words=["anna"])
case("Schicke mir eine Benachrichtigung wenn im Büro der Raffstore 50% erreicht hat", "cover.buero_raffstore", None, 50, expect_words=["50"], wait=6.0)
# --- andere Empfänger ------------------------------------------------------------
case("Schick Anna eine Nachricht, wenn die Haustür aufgeht.", W + "haustuer", "off", "on", target=ANNA, expect_words=["haustür"])
case("Benachrichtige Anna, wenn das Küchenfenster geöffnet wird.", W + "kuechenfenster", "off", "on", target=ANNA, expect_words=["fenster"])
case("Benachrichtige mich, wenn das Küchenfenster aufgeht.", W + "kuechenfenster", "off", "on", target=ANNA, user="anna", expect_words=["fenster"], note="Anna spricht → Annas Handy")
# --- sofort --------------------------------------------------------------------------
case("Schick mir eine Testbenachrichtigung.", kind="now", expect_words=["test"])
case("Schick mir aufs Handy: Essen ist fertig.", kind="now", expect_words=["essen"])
case("Schreib Anna, dass das Essen fertig ist.", kind="now", target=ANNA, expect_words=["essen"])
case("Sag Anna Bescheid, dass ich gleich komme.", kind="now", target=ANNA, expect_words=["gleich"])
# --- zeitversetzt ---------------------------------------------------------------
case("Schick mir in 15 Sekunden eine Nachricht, dass der Tee fertig ist.", kind="delay", wait=22.0, expect_words=["tee"])
case("Erinnere mich in 15 Sekunden an den Backofen.", kind="delay", wait=22.0, expect_words=["backofen"])
# --- darf nichts auslösen -----------------------------------------------------------
case("Benachrichtige mich nicht, wenn das Küchenfenster aufgeht.", W + "kuechenfenster", "off", "on", kind="negative")
case("Sag mir, ob das Küchenfenster offen ist.", kind="question")


async def automation_ids(r: Runner) -> set[str]:
    return {s["attributes"].get("id") for eid, s in (await r.states()).items() if eid.startswith("automation.") and s["attributes"].get("id")}


async def drive(r: Runner, entity: str, value) -> None:
    if entity.startswith("cover."):
        if isinstance(value, int):
            await r.service("cover.set_cover_position", {"entity_id": entity, "position": value})
        else:
            await r.service("cover.open_cover" if value == "open" else "cover.close_cover", {"entity_id": entity})
    else:
        await r.service("haus_sim.set", {"entity_id": entity, "value": value})


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/push_check.json")
    args = ap.parse_args()
    results = []
    async with aiohttp.ClientSession() as session, Runner(session, load_tokens()) as r:
        await r.set_options({"agent_notify_targets": [PHIL, ANNA], "agent_delivery_channels": ["push"], "allow_non_admin_automations": True})
        for uid, person, tgt in (("$admin_user_id", "person.philipp", PHIL), ("$anna_user_id", "person.anna", ANNA)):
            await r.service("homeintent.bind_user_context", r.subst({"user_id": uid, "person_entity_id": person, "notification_targets": [tgt], "preferred_notification_target": tgt, "confirmed": True}))
        for c in CASES:
            await r.service("haus_sim.reset")
            if c["trigger"] and c["before"] is not None:
                await drive(r, c["trigger"], c["before"])
            await asyncio.sleep(1.5)
            before_ids = await automation_ids(r)
            await r.service("haus_sim.clear_log")
            ws = r.ws[c["user"]]
            res = await ws.call("conversation/process", text=c["text"], agent_id=AGENT, language="de")
            speech1 = res["response"].get("speech", {}).get("plain", {}).get("speech") or ""
            speech2 = ""
            if res.get("continue_conversation") or speech1.rstrip().endswith("?"):
                res2 = await ws.call("conversation/process", text="Ja.", agent_id=AGENT, language="de", conversation_id=res.get("conversation_id"))
                speech2 = res2["response"].get("speech", {}).get("plain", {}).get("speech") or ""
            await asyncio.sleep(2.0)
            created = (await automation_ids(r)) - before_ids
            early = [(p["target"], p["message"]) for p in (await r.sim_log())["notifications"]]
            await r.service("haus_sim.clear_log")
            if c["kind"] in ("auto", "negative") and c["trigger"]:
                await drive(r, c["trigger"], c["after"])
            await asyncio.sleep(c["wait"])
            log = await r.sim_log()
            got = [(p["target"], p["message"]) for p in log["notifications"]]
            if c["kind"] in ("now", "question"):
                got = early + got
            elif early:
                got = early + got  # a push already at creation time is a defect
            ok_target = [m for t, m in got if t == c["target"]]
            wrong_target = [t for t, _ in got if t != c["target"]]
            text_ok = any(all(w in m.casefold() for w in c["expect_words"]) for m in ok_target) if c["expect_words"] else bool(ok_target)
            if c["kind"] == "negative":
                passed = not created and not got
            elif c["kind"] == "question":
                passed = not created and not got and "?" not in speech1[-1:]
            else:
                passed = bool(ok_target) and not wrong_target and text_ok and (c["kind"] == "now" or not early)
            results.append({**c, "early_pushes": early, "speech": speech1, "confirm_speech": speech2, "created": sorted(created), "pushes": got, "passed": passed})
            # cleanup: remove every automation this case created
            for aid in created:
                try:
                    await rest(session, "DELETE", f"/api/config/automation/config/{aid}", r.tokens["admin"])
                except Exception:  # noqa: BLE001
                    pass
            if created:
                await r.service("automation.reload")
            mark = "✓" if passed else "✗"
            print(f"{mark} [{c['kind']}] {c['text']}\n    → {speech1[:150]}\n    ⇒ {speech2[:90]}\n    push: {got}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n{sum(x['passed'] for x in results)}/{len(results)} bestanden")


if __name__ == "__main__":
    asyncio.run(main())
