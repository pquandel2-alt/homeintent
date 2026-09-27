"""Exercise the Learning Center websocket API and panel as admin and non-admin."""
import asyncio, json
import aiohttp
from haclient import BASE, WS, HAError, load_tokens

P = "homeintent/learning_center/"


async def main() -> None:
    t = load_tokens()
    async with aiohttp.ClientSession() as s:
        async with s.get(BASE + "/homeintent") as r:
            print("panel /homeintent:", r.status)
        for user in ("admin", "anna"):
            async with WS(s, t[user]) as ws:
                panels = await ws.call("get_panels")
                print(user, "panel registered:", "homeintent" in panels, (panels.get("homeintent") or {}).get("require_admin"))
                for cmd, extra in (("entries", {}), ("summary", {}), ("models/list", {}), ("permissions/list", {}), ("mutes/list", {}), ("history/list", {}), ("tombstones/list", {})):
                    try:
                        res = await ws.call(P + cmd, **extra)
                        print(f"  {user} {cmd}: ok", json.dumps(res, ensure_ascii=False)[:260])
                    except HAError as err:
                        print(f"  {user} {cmd}: ERROR {err}")
                try:
                    await ws.call(P + "models/reset", confirm=True)
                    print(f"  {user} models/reset: ok")
                except HAError as err:
                    print(f"  {user} models/reset: {err}")


asyncio.run(main())
