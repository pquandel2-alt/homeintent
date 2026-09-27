"""Run every example sentence from README.md code blocks against the live house.

python readme_check.py [--readme ../README.md] [--out results/readme_check.json]

Dialog blocks (lines starting with "Du:") run as one conversation; every other
example runs alone on a reset house. Confirmation questions are answered with
"Nein." so that examples do not leave automations behind. Verdict per sentence:
  ok    – understood (action, answer or a confirmation/preview question)
  ask   – asked a clarification that is not a confirmation (acceptable for
          ambiguous examples, reviewed manually)
  fail  – "nicht verstanden", "nicht gefunden", errors, unsupported
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import aiohttp

from haclient import load_tokens
from runner import AGENT, Runner

FAIL_MARKERS = (
    "nicht verstanden", "nicht gefunden", "nicht eindeutig unterstützt", "kein eindeutig passendes",
    "ziel nicht gefunden", "fehler beim", "konnte trigger und aktion", "keine passenden ergebnisse",
    "nicht unterstützt", "kann ich nicht", "nicht zuordnen",
)
SKIP_SECTIONS = {"Architektur", "Entwicklung und Tests", "Reproduzierbarer Benchmark", "Wie HomeIntent Sprache versteht"}
CONFIRM_RE = re.compile(r"\b(soll ich|soll die|soll diese|soll das|möchtest du|wirklich)\b", re.I)


def blocks(readme: str) -> list[dict]:
    out, head, cur, inblock = [], "", None, False
    for line in readme.splitlines():
        if line.startswith("#") and not inblock:
            head = line.lstrip("# ").strip()
        if line.startswith("```"):
            if not inblock:
                inblock = True
                cur = {"section": head, "lang": line[3:].strip(), "lines": []}
            else:
                inblock = False
                if cur["lang"] in ("text", "") and head not in SKIP_SECTIONS and cur["lines"]:
                    out.append(cur)
            continue
        if inblock:
            cur["lines"].append(line)
    result = []
    for b in out:
        dialog = any(l.strip().startswith("Du:") for l in b["lines"])
        sents = []
        for l in b["lines"]:
            s = l.strip()
            if not s or s.startswith(("Assist:", "→")) or l.startswith(("        ", "\t")):
                continue
            s = re.sub(r"^Du:\s*", "", s)
            if dialog and not l.strip().startswith("Du:"):
                continue
            if len(s) >= 4:
                sents.append(s)
        if sents:
            result.append({"section": b["section"], "dialog": dialog, "sentences": sents})
    return result


def verdict(speech: str, asked: bool) -> str:
    low = speech.casefold()
    if any(m in low for m in FAIL_MARKERS):
        return "fail"
    if asked and not CONFIRM_RE.search(speech) and not low.startswith(("planvorschau", "automation erkannt", "vorschau")):
        return "ask"
    return "ok"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default=str(Path(__file__).resolve().parent.parent / "README.md"))
    ap.add_argument("--out", default="results/readme_check.json")
    args = ap.parse_args()
    bl = blocks(Path(args.readme).read_text(encoding="utf-8"))
    results = []
    async with aiohttp.ClientSession() as session, Runner(session, load_tokens()) as r:
        # Realistic household setup: push target bound to Philipp, TTS for timers.
        await r.set_options({
            "agent_notify_targets": ["notify.handy_philipp_nachricht", "notify.handy_anna_nachricht"],
            "agent_tts_entity": "tts.sprachausgabe", "agent_media_players": ["media_player.kuechenradio"],
            "memory_enabled": True,
        })
        await r.service("homeintent.bind_user_context", r.subst({"user_id": "$admin_user_id", "person_entity_id": "person.philipp", "notification_targets": ["notify.handy_philipp_nachricht"], "preferred_notification_target": "notify.handy_philipp_nachricht", "confirmed": True}))
        await r.service("homeintent.bind_user_context", r.subst({"user_id": "$anna_user_id", "person_entity_id": "person.anna", "notification_targets": ["notify.handy_anna_nachricht"], "preferred_notification_target": "notify.handy_anna_nachricht", "confirmed": True}))
        for b in bl:
            groups = [b["sentences"]] if b["dialog"] else [[s] for s in b["sentences"]]
            for group in groups:
                await r.service("haus_sim.reset")
                conv = None
                for text in group:
                    await r.service("haus_sim.clear_log")
                    res = await r.admin.call("conversation/process", text=text, agent_id=AGENT, language="de", conversation_id=conv)
                    conv = res.get("conversation_id")
                    speech = res["response"].get("speech", {}).get("plain", {}).get("speech") or ""
                    asked = bool(res.get("continue_conversation")) or speech.rstrip().endswith("?")
                    await asyncio.sleep(0.6)
                    log = await r.sim_log()
                    v = verdict(speech, asked)
                    results.append({"section": b["section"], "text": text, "speech": speech, "verdict": v,
                                    "calls": sorted({c["entity_id"] + ":" + c["action"] for c in log["calls"]})})
                    print(f"{ {'ok': '✓', 'ask': '?', 'fail': '✗'}[v]} [{b['section'][:28]}] {text}\n      → {speech[:140]}")
                    if not b["dialog"] and asked and CONFIRM_RE.search(speech):
                        await r.admin.call("conversation/process", text="Nein.", agent_id=AGENT, language="de", conversation_id=conv)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    from collections import Counter
    c = Counter(x["verdict"] for x in results)
    print(f"\nGesamt {len(results)}: ok {c['ok']}, Rückfrage {c['ask']}, fehlgeschlagen {c['fail']}")
    for sec in dict.fromkeys(x["section"] for x in results):
        cc = Counter(x["verdict"] for x in results if x["section"] == sec)
        print(f"  {sec}: {cc['ok']} ok / {cc['ask']} ? / {cc['fail']} ✗")


if __name__ == "__main__":
    asyncio.run(main())
