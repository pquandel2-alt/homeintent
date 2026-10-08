"""Bring a fresh HA config dir into the realistic test-house state.

Idempotent enough to be re-run: onboarding, users, integrations, persons,
Assist exposure and 10 days of recorder statistics/history.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta, datetime, timezone
import json
import math
import random

import aiohttp

from haclient import CLIENT_ID, TOKENS, WS, HAError, login, rest, wait_for_ha

ADMIN = ("philipp", "Philipp", "testhaus-admin")
MEMBER = ("anna", "Anna", "testhaus-anna")
CHILD = ("lena", "Lena", "testhaus-lena")


async def onboarding(session: aiohttp.ClientSession) -> str:
    try:
        status = await rest(session, "GET", "/api/onboarding")
    except HAError:
        status = [{"step": s, "done": True} for s in ("user", "core_config", "analytics", "integration")]
    done = {step["step"] for step in status if step["done"]}
    if "user" not in done:
        res = await rest(
            session, "POST", "/api/onboarding/users",
            json={"client_id": CLIENT_ID, "name": ADMIN[1], "username": ADMIN[0], "password": ADMIN[2], "language": "de"},
        )
        tok = await rest(
            session, "POST", "/auth/token",
            data={"grant_type": "authorization_code", "code": res["auth_code"], "client_id": CLIENT_ID},
        )
        token = tok["access_token"]
    else:
        token = await login(session, ADMIN[0], ADMIN[2])
    for step, body in (
        ("core_config", {}),
        ("analytics", {}),
        ("integration", {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID}),
    ):
        if step not in done:
            await rest(session, "POST", f"/api/onboarding/{step}", token, json=body)
    return token


async def ensure_user(ws: WS, session, spec, admin: bool) -> str:
    username, name, password = spec
    users = await ws.call("config/auth/list")
    user = next((u for u in users if u["name"] == name), None)
    if user is None:
        user = (await ws.call("config/auth/create", name=name, group_ids=["system-admin" if admin else "system-users"], local_only=False))["user"]
        await ws.call("config/auth_provider/homeassistant/create", user_id=user["id"], username=username, password=password)
    return user["id"]


async def ensure_entry(session, token: str, handler: str, answers: dict | None = None) -> bool:
    entries = await rest(session, "GET", "/api/config/config_entries/entry", token)
    title = (answers or {}).get("calendar_name") or (answers or {}).get("todo_list_name")
    if any(e["domain"] == handler and (title is None or e["title"] == title) for e in entries):
        return False
    # A cold Home Assistant (first CI start) may still be installing the
    # requirements of an integration's dependencies; retry briefly.
    for attempt in range(90):
        try:
            flow = await rest(session, "POST", "/api/config/config_entries/flow", token, json={"handler": handler})
            break
        except HAError as err:
            if "Invalid handler" not in str(err) or attempt == 89:
                raise
            await asyncio.sleep(2)
    if flow.get("type") == "form":
        flow = await rest(session, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}", token, json=answers or {})
    if flow.get("type") != "create_entry":
        raise HAError(f"{handler}: {flow}")
    return True


async def remove_online_weather(session, token: str) -> None:
    """The onboarding adds the met.no forecast (internet). Where it is
    reachable (CI) the house would have two weather entities and every
    weather question would rightly ask which one; the test bed keeps only
    the simulated, settable ``weather.zuhause`` (7.9.3 B1)."""
    entries = await rest(session, "GET", "/api/config/config_entries/entry", token)
    for entry in entries:
        if entry["domain"] == "met":
            await rest(session, "DELETE", f"/api/config/config_entries/entry/{entry['entry_id']}", token)
            print(f"met.no-Wetter entfernt ({entry['title']})")


async def existing_installation_style(session, token: str) -> None:
    """7.9.3 B5: a new installation starts with the confirmation tone.

    The test bed checks that once and then behaves like an installation
    updated from 7.9.2, which keeps its stored ``response_style``
    ("spoken"): the catalogue checks the spoken wording, tone scenarios
    switch to ``tone`` themselves.
    """
    entries = await rest(session, "GET", "/api/config/config_entries/entry", token)
    entry = next(e for e in entries if e["domain"] == "homeintent")
    flow = await rest(session, "POST", "/api/config/config_entries/options/flow", token, json={"handler": entry["entry_id"]})
    values = {}
    for field in flow["data_schema"]:
        desc = field.get("description") or {}
        if "suggested_value" in desc:
            values[field["name"]] = desc["suggested_value"]
        elif "default" in field:
            values[field["name"]] = field["default"]
    print(f"response_style der neuen Installation: {values.get('response_style')!r}")
    if values.get("response_style") != "tone":
        raise HAError(f"Neue Installation ohne Bestätigungston: {values.get('response_style')!r}")
    data = {k: v for k, v in values.items() if v is not None}
    data["response_style"] = "spoken"
    res = await rest(session, "POST", f"/api/config/config_entries/options/flow/{flow['flow_id']}", token, json=data)
    if res.get("type") != "create_entry":
        raise HAError(f"options rejected: {res}")
    await asyncio.sleep(3)


async def import_history(ws: WS) -> None:
    """10 days of hourly statistics for numeric sensors (mean/min/max)."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rnd = random.Random(42)
    specs = {
        "sensor.temperatur_wohnzimmer": (20.8, 1.2, "°C"),
        "sensor.temperatur_kueche": (20.5, 1.0, "°C"),
        "sensor.temperatur_buero": (19.8, 1.4, "°C"),
        "sensor.temperatur_schlafzimmer": (18.1, 0.8, "°C"),
        "sensor.aussentemperatur": (11.0, 5.0, "°C"),
        "sensor.luftfeuchtigkeit_badezimmer": (62.0, 8.0, "%"),
        "sensor.stromverbrauch_haus": (480.0, 300.0, "W"),
    }
    for statistic_id, (base, amp, unit) in specs.items():
        stats = []
        for h in range(24 * 10, 1, -1):
            start = now - timedelta(hours=h)
            local_hour = (start.hour + 2) % 24
            day_shift = -0.4 * ((h // 24) % 3)
            value = base + day_shift + amp * math.sin((local_hour - 9) / 24 * 2 * math.pi) + rnd.uniform(-0.2, 0.2) * amp
            stats.append({
                "start": start.isoformat(),
                "mean": round(value, 2),
                "min": round(value - 0.3 * amp, 2),
                "max": round(value + 0.3 * amp, 2),
            })
        await ws.call(
            "recorder/import_statistics",
            metadata={
                "has_mean": True,
                "mean_type": 1,
                "has_sum": False,
                "name": None,
                "source": "recorder",
                "statistic_id": statistic_id,
                "unit_of_measurement": unit,
                "unit_class": None,
            },
            stats=stats,
        )
    energy = []
    total = 18000.0
    for h in range(24 * 10, 1, -1):
        start = now - timedelta(hours=h)
        total += 0.2 + rnd.uniform(0, 0.8)
        energy.append({"start": start.isoformat(), "state": round(total, 2), "sum": round(total - 18000.0, 2)})
    await ws.call(
        "recorder/import_statistics",
        metadata={
            "has_mean": False, "mean_type": 0, "has_sum": True, "name": None, "source": "recorder",
            "statistic_id": "sensor.energiezaehler", "unit_of_measurement": "kWh", "unit_class": "energy",
        },
        stats=energy,
    )


async def main() -> None:
    await wait_for_ha()
    async with aiohttp.ClientSession() as session:
        admin_token = await onboarding(session)
        async with WS(session, admin_token) as ws:
            admin_id = next(u["id"] for u in await ws.call("config/auth/list") if u["name"] == ADMIN[1])
            anna_id = await ensure_user(ws, session, MEMBER, admin=False)
            lena_id = await ensure_user(ws, session, CHILD, admin=False)
            llat = await ws.call("auth/long_lived_access_token", client_name=f"sim-{datetime.now().timestamp()}", lifespan=365)

        await remove_online_weather(session, llat)
        await ensure_entry(session, llat, "haus_sim")
        await ensure_entry(session, llat, "local_calendar", {"calendar_name": "Familie"})
        await ensure_entry(session, llat, "local_calendar", {"calendar_name": "Müllabfuhr"})
        await ensure_entry(session, llat, "local_todo", {"todo_list_name": "Arbeitsliste"})
        created = await ensure_entry(session, llat, "homeintent")
        await asyncio.sleep(3)
        if created:
            await existing_installation_style(session, llat)

        async with WS(session, llat) as ws:
            persons = await ws.call("person/list")
            existing = {p["name"]: p for p in persons["storage"]}
            for name, uid, tracker in (("Philipp", admin_id, "device_tracker.handy_philipp"), ("Anna", anna_id, "device_tracker.handy_anna"), ("Lena", lena_id, None)):
                trackers = [tracker] if tracker else []
                if name not in existing:
                    await ws.call("person/create", name=name, user_id=uid, device_trackers=trackers)
                else:
                    await ws.call("person/update", person_id=existing[name]["id"], name=name, user_id=uid, device_trackers=trackers)
            states = await ws.call("get_states")
            skip_domains = {"sun", "zone", "conversation", "tts", "event", "update", "stt", "assist_satellite", "wake_word", "ai_task"}
            entity_ids = [s["entity_id"] for s in states if s["entity_id"].split(".")[0] not in skip_domains]
            await ws.call("homeassistant/expose_entity", assistants=["conversation"], entity_ids=entity_ids, should_expose=True)
            await import_history(ws)
            agents = await ws.call("conversation/agent/list", language="de")

        async def llat_for(spec) -> str:
            short = await login(session, spec[0], spec[2])
            async with WS(session, short) as user_ws:
                return await user_ws.call("auth/long_lived_access_token", client_name=f"sim-{spec[0]}-{datetime.now().timestamp()}", lifespan=365)

        anna_token = await llat_for(MEMBER)
        lena_token = await llat_for(CHILD)
        TOKENS.write_text(json.dumps({
            "admin": llat, "anna": anna_token, "lena": lena_token,
            "admin_user_id": admin_id, "anna_user_id": anna_id, "lena_user_id": lena_id,
        }, indent=2))
        print("agents:", [(a["id"], a["name"]) for a in agents["agents"]])
        print("exposed:", len(entity_ids))


if __name__ == "__main__":
    asyncio.run(main())
