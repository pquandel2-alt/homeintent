"""Interactive probe: python say.py [--user anna] [--device <area>] "Satz" ["Folgesatz" ...]

All sentences share one conversation id. Prints the speech and the device
calls the simulated house received for each turn.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import aiohttp

from haclient import WS, load_tokens

AGENT = "conversation.homeintent"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="admin")
    ap.add_argument("--device", help="device id to send as Assist device")
    ap.add_argument("--wait", type=float, default=0.5)
    ap.add_argument("sentences", nargs="+")
    args = ap.parse_args()
    tokens = load_tokens()
    async with aiohttp.ClientSession() as session:
        async with WS(session, tokens["admin"]) as admin, WS(session, tokens[args.user]) as ws:
            conv_id = None
            for text in args.sentences:
                await admin.call("call_service", domain="haus_sim", service="clear_log")
                payload = {"text": text, "agent_id": AGENT, "language": "de", "conversation_id": conv_id}
                if args.device:
                    payload["device_id"] = args.device
                res = await ws.call("conversation/process", **payload)
                conv_id = res.get("conversation_id")
                await asyncio.sleep(args.wait)
                log = (await admin.call("call_service", domain="haus_sim", service="get_log", return_response=True))["response"]
                speech = res["response"]["speech"].get("plain", {}).get("speech")
                print(f"> {text}\n< [{res['response']['response_type']}] {speech}")
                for call in log["calls"]:
                    print(f"    call {call['entity_id']} {call['action']} {json.dumps(call['data'], ensure_ascii=False) if call['data'] else ''}")
                for n in log["notifications"]:
                    print(f"    notify {n['target']}: {n['message']}")


if __name__ == "__main__":
    asyncio.run(main())
