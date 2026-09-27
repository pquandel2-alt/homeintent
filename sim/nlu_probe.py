"""LLM-level understanding probe: python nlu_probe.py [--out results/nlu_probe.json]

Each case is a sentence a human (or an LLM) would understand effortlessly.
``ideal`` is what a person would expect:
  act     – carry out the (unambiguous) action, ``calls`` lists acceptable targets
  ask     – a sensible clarification / proposal question, no action yet
  answer  – a factual answer, ``any`` lists words the answer should contain
  none    – nothing must happen (negation, hypothetical)
Scoring per case: ok (matches ideal), partial (sensible ask instead of act),
fail (not understood / wrong action / wrong answer).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import aiohttp

from haclient import WS, load_tokens

import os
AGENT = os.environ.get("AGENT", "conversation.homeintent")

C = []


def case(cat, text, ideal, calls=(), any_=(), device=None, pre=(), note=""):
    C.append({"cat": cat, "text": text, "ideal": ideal, "calls": list(calls), "any": list(any_),
              "device": device, "pre": list(pre), "note": note})


# A — indirekte Wünsche und Zustandsaussagen
case("indirekt", "Mir ist kalt.", "act", ["climate.heizung_wohnzimmer"], device="Wohnzimmer")
case("indirekt", "Es ist zu dunkel hier.", "act", ["light.buerolicht", "light.schreibtischlampe"], device="Büro")
case("indirekt", "Hier ist es zu hell.", "act", ["light.stehlampe", "light.wohnzimmer_deckenlicht"], device="Wohnzimmer", pre=["Schalte die Stehlampe und das Wohnzimmer Deckenlicht ein."])
case("indirekt", "Ich gehe jetzt schlafen.", "ask")
case("indirekt", "Ich verlasse jetzt das Haus.", "ask")
case("indirekt", "Im Bad ist es total stickig.", "act", ["fan.badluefter"])
case("indirekt", "Die Sonne blendet im Büro.", "act", ["cover.buero_raffstore"])
case("indirekt", "Ich will einen Film schauen.", "act", ["scene.filmabend"])
case("indirekt", "Ich hätte gern etwas mehr Wärme im Bad.", "act", ["climate.heizung_badezimmer"])
case("indirekt", "Ich brauche das Licht im Flur nicht mehr.", "act", ["light.flurlicht"], pre=["Schalte das Flurlicht ein."])
case("indirekt", "Das Licht im Büro kann aus.", "act", ["light.buerolicht"], pre=["Schalte das Bürolicht ein."])
case("indirekt", "Es ist so laut in der Küche.", "act", ["media_player.kuechenradio"])

# B — Fragen mit Schlussfolgerung
case("schluss", "Muss ich lüften?", "answer", any_=["bad", "feucht", "co2", "luftfeuchtigkeit"])
case("schluss", "Brennt irgendwo noch Licht?", "answer", any_=["kein", "nein", "licht"])
case("schluss", "Ist das Haus abgeschlossen?", "answer", any_=["haustür", "schloss", "verriegelt", "abgeschlossen"])
case("schluss", "Ist unten noch irgendwas an?", "answer", any_=["küchenradio", "steckdose", "fernseher", "heizung"])
case("schluss", "Wo ist es gerade am kältesten?", "answer", any_=["keller", "schlafzimmer"])
case("schluss", "Hab ich vergessen, irgendwas auszuschalten?", "answer", any_=["küchenradio", "steckdose", "nichts", "eingeschaltet"])
case("schluss", "Warum ist es im Büro so kalt?", "answer", any_=["heiz", "grad", "fenster"])
case("schluss", "Wie lange läuft die Waschmaschine noch?", "answer", any_=["minute", "uhr"])
case("schluss", "Sind alle Fenster zu?", "answer", any_=["nein", "küche", "bad"])
case("schluss", "Kann ich die Heizung im Bad ausmachen, das Fenster ist doch offen?", "act", ["climate.heizung_badezimmer"])
case("schluss", "Was ist gerade los im Haus?", "answer", any_=["fenster", "an", "offen"])
case("schluss", "Ist jemand im Wohnzimmer?", "answer", any_=["ja", "präsenz", "belegt", "anwesend"])

# C — Umgangssprache, Synonyme, verkürzt
case("umgang", "Mach mal Licht in der Küche.", "act", ["light.kuechenlicht", "light.kuecheninsel"])
case("umgang", "Licht aus im Flur.", "act", ["light.flurlicht"], pre=["Schalte das Flurlicht ein."])
case("umgang", "Kannste die Rollos in der Küche runtermachen?", "act", ["cover.kuechenrollladen"])
case("umgang", "Mach die Jalousie im Büro zu.", "act", ["cover.buero_raffstore"])
case("umgang", "Dreh die Heizung im Wohnzimmer bisschen hoch.", "act", ["climate.heizung_wohnzimmer"])
case("umgang", "Mach die Glotze an.", "act", ["media_player.wohnzimmer_tv"])
case("umgang", "Mach die Musik in der Küche leiser.", "act", ["media_player.kuechenradio"])
case("umgang", "Mach's Radio aus.", "act", ["media_player.kuechenradio"])
case("umgang", "Lampe im Büro an.", "act", ["light.buerolicht", "light.schreibtischlampe"])
case("umgang", "Zieh die Rollläden im Schlafzimmer ganz hoch.", "act", ["cover.schlafzimmer_rollladen"], pre=["Fahre den Schlafzimmer Rollladen auf 20 Prozent."])
case("umgang", "Fernseher aus.", "act", ["media_player.wohnzimmer_tv"], pre=["Schalte den Wohnzimmer TV ein."])
case("umgang", "Kaffee!", "act", ["switch.kaffeemaschine", "script.kaffee_kochen"])
case("umgang", "Staubsauger an.", "act", ["vacuum.saugroboter"])
case("umgang", "Tür zu.", "ask")

# D — Mehrfachbefehle
case("mehrfach", "Mach das Licht im Flur an und die Rollläden in der Küche runter.", "act", ["light.flurlicht", "cover.kuechenrollladen"], note="beide")
case("mehrfach", "Schalte das Küchenlicht aus und die Kaffeemaschine an.", "act", ["light.kuechenlicht", "switch.kaffeemaschine"], pre=["Schalte das Küchenlicht ein."], note="beide")
case("mehrfach", "Mach im Wohnzimmer alles aus.", "act", ["light.stehlampe", "media_player.wohnzimmer_tv", "switch.fernseher_steckdose"], pre=["Schalte die Stehlampe ein."])
case("mehrfach", "Mach das Bürolicht an, stell die Heizung im Büro auf 21 Grad und fahr den Raffstore hoch.", "act", ["light.buerolicht", "climate.heizung_buero", "cover.buero_raffstore"], note="alle drei")

# E — Kontext, Pronomen, Korrektur
case("kontext", "Etwas heller bitte.", "act", ["light.buerolicht"], pre=["Mach das Bürolicht an."])
case("kontext", "Nee, doch wieder aus.", "act", ["light.buerolicht"], pre=["Mach das Bürolicht an."])
case("kontext", "Mach's da wärmer.", "act", ["climate.heizung_schlafzimmer"], pre=["Wie warm ist es im Schlafzimmer?"])
case("kontext", "Dann mach die Heizung da aus.", "act", ["climate.heizung_kueche"], pre=["Ist das Küchenfenster offen?"])
case("kontext", "Und im Kinderzimmer auch.", "act", ["light.kinderzimmerlicht"], pre=["Mach das Licht im Schlafzimmer an."])
case("kontext", "Die andere.", "act", ["light.nachttischlampe_rechts"], pre=["Mach die linke Nachttischlampe an."])
case("kontext", "Mach alle aus.", "act", ["light.kuechenlicht", "light.flurlicht"], pre=["Schalte das Küchenlicht und das Flurlicht ein.", "Welche Lichter sind an?"], note="beide")

# F — Zeit und Bedingung in Alltagssprache
case("zeit", "Weck mich morgen um 7 mit Licht im Schlafzimmer.", "ask")
case("zeit", "Schalte die Kaffeemaschine in 10 Minuten wieder aus.", "ask")
case("zeit", "Mach das Außenlicht an, sobald es dunkel wird.", "ask")
case("zeit", "Sag mir Bescheid, wenn die Waschmaschine fertig ist.", "ask")
case("zeit", "Wenn ich nach Hause komme, soll das Flurlicht angehen.", "ask")
case("zeit", "Mach um halb sieben die Rollläden hoch.", "ask")

# G — Negation, Konjunktiv, Hypothetisch
case("negation", "Lass das Licht in der Küche an.", "none", pre=["Schalte das Küchenlicht ein."])
case("negation", "Nicht das Küchenlicht, das Esszimmerlicht meine ich.", "act", ["light.esszimmer_pendelleuchte"], pre=["Schalte das Küchenlicht ein."])
case("negation", "Könntest du vielleicht irgendwann mal die Markise einfahren?", "act", ["cover.markise"], pre=["Fahre die Markise aus."])
case("negation", "Ich frage mich, ob die Haustür abgeschlossen ist.", "answer", any_=["verriegelt", "abgeschlossen", "haustürschloss"])
case("negation", "Die Stehlampe muss nicht an sein.", "act", ["light.stehlampe"], pre=["Schalte die Stehlampe ein."])

# H — Auskunft über das Haus
case("auskunft", "Was kann ich im Wohnzimmer alles steuern?", "answer", any_=["stehlampe", "rollladen", "tv"])
case("auskunft", "Welche Szenen gibt es?", "answer", any_=["filmabend"])
case("auskunft", "Was macht das Skript Gute Nacht?", "answer", any_=["licht", "schloss", "aus"])
case("auskunft", "Welche Räume gibt es im Obergeschoss?", "answer", any_=["schlafzimmer", "kinderzimmer", "bad"])
case("auskunft", "Wie viele Lampen hat das Haus?", "answer", any_=["23"])
case("auskunft", "Wofür ist der Heizlüfter?", "answer", any_=["bad", "heiz"])


async def run(out: Path) -> None:
    tokens = load_tokens()
    results = []
    async with aiohttp.ClientSession() as session, WS(session, tokens["admin"]) as ws:
        devs = await ws.call("config/device_registry/list")
        areas = {a["area_id"]: a["name"] for a in await ws.call("config/area_registry/list")}
        area_dev = {}
        for d in devs:
            if d.get("area_id") and d.get("manufacturer") == "Haus-Simulation" and d.get("model") == "light":
                area_dev.setdefault(areas[d["area_id"]], d["id"])

        async def say(text, conv, device):
            payload = {"text": text, "agent_id": AGENT, "language": "de", "conversation_id": conv}
            if device:
                payload["device_id"] = area_dev[device]
            return await ws.call("conversation/process", **payload)

        async def svc(domain, service, data=None, response=False):
            p = {"domain": domain, "service": service, "service_data": data or {}}
            if response:
                p["return_response"] = True
            return await ws.call("call_service", **p)

        for c in C:
            await svc("haus_sim", "reset")
            await asyncio.sleep(0.3)
            conv = None
            for p in c["pre"]:
                r = await say(p, conv, c["device"])
                conv = r.get("conversation_id")
                await asyncio.sleep(0.4)
                if r.get("continue_conversation"):
                    r = await say("Ja.", conv, c["device"])
            await asyncio.sleep(3.5 if c["pre"] else 0.2)  # let covers settle
            await svc("haus_sim", "clear_log")
            r = await say(c["text"], conv, c["device"])
            await asyncio.sleep(1.0)
            log = (await svc("haus_sim", "get_log", response=True))["response"]
            calls = sorted({f"{x['entity_id']}" for x in log["calls"]})
            resp = r["response"]
            speech = resp.get("speech", {}).get("plain", {}).get("speech") or ""
            asked = bool(r.get("continue_conversation")) or speech.rstrip().endswith("?")
            error = resp.get("response_type") == "error"
            ideal = c["ideal"]
            hit = [e for e in c["calls"] if e in calls]
            if ideal == "act":
                need = len(c["calls"]) if c["note"] in ("beide", "alle drei") else 1
                if len(hit) >= need and not error:
                    verdict = "ok"
                elif asked and not calls:
                    verdict = "partial"
                else:
                    verdict = "fail"
            elif ideal == "ask":
                verdict = "ok" if asked and not calls else ("partial" if not error and not calls else "fail")
            elif ideal == "answer":
                low = speech.casefold()
                verdict = "ok" if (not error and not calls and any(a in low for a in c["any"])) else ("partial" if asked and not calls else "fail")
            else:  # none
                verdict = "ok" if not calls else "fail"
            results.append({**c, "speech": speech, "type": resp.get("response_type"), "asked": asked, "got_calls": calls, "verdict": verdict})
            mark = {"ok": "✓", "partial": "~", "fail": "✗"}[verdict]
            print(f"{mark} [{c['cat']}] {c['text']}\n    → {speech[:150]}  {calls if calls else ''}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    from collections import Counter
    total = Counter(x["verdict"] for x in results)
    print("\nGesamt:", dict(total), "von", len(results))
    for cat in dict.fromkeys(x["cat"] for x in results):
        cc = Counter(x["verdict"] for x in results if x["cat"] == cat)
        print(f"  {cat}: ok {cc['ok']}, teilweise {cc['partial']}, fehlgeschlagen {cc['fail']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/nlu_probe.json")
    asyncio.run(run(Path(ap.parse_args().out)))
