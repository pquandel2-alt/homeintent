"""Tiny async Home Assistant client (REST + WebSocket) for the live test bed."""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

import aiohttp

BASE = "http://127.0.0.1:8123"
CLIENT_ID = f"{BASE}/"
TOKENS = Path(__file__).resolve().parent / "config" / "sim_tokens.json"


class HAError(RuntimeError):
    pass


class WS:
    """Authenticated websocket connection."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token
        self._ids = itertools.count(1)
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def __aenter__(self) -> "WS":
        self.ws = await self._session.ws_connect(f"{BASE}/api/websocket", max_msg_size=0, heartbeat=20)
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": self._token})
        msg = await self.ws.receive_json()
        if msg["type"] != "auth_ok":
            raise HAError(msg)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.ws:
            await self.ws.close()

    async def call(self, type_: str, **payload: Any) -> Any:
        assert self.ws
        msg_id = next(self._ids)
        await self.ws.send_json({"id": msg_id, "type": type_, **payload})
        while True:
            msg = await self.ws.receive_json(timeout=120)
            if msg.get("id") != msg_id:
                continue
            if msg["type"] == "result":
                if not msg["success"]:
                    raise HAError(f"{type_}: {msg['error']}")
                return msg["result"]

    async def run_pipeline(self, text: str, **payload: Any) -> list[dict[str, Any]]:
        """Run an assist pipeline in text mode and collect its events."""
        assert self.ws
        msg_id = next(self._ids)
        await self.ws.send_json(
            {
                "id": msg_id,
                "type": "assist_pipeline/run",
                "start_stage": "intent",
                "end_stage": "intent",
                "input": {"text": text},
                **payload,
            }
        )
        events: list[dict[str, Any]] = []
        while True:
            msg = await self.ws.receive_json(timeout=120)
            if msg.get("id") != msg_id:
                continue
            if msg["type"] == "result" and not msg["success"]:
                raise HAError(msg["error"])
            if msg["type"] == "event":
                events.append(msg["event"])
                if msg["event"]["type"] in ("run-end", "error"):
                    return events


async def rest(session: aiohttp.ClientSession, method: str, path: str, token: str | None = None, **kw: Any) -> Any:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with session.request(method, BASE + path, headers=headers, **kw) as resp:
        text = await resp.text()
        if resp.status >= 400:
            raise HAError(f"{method} {path}: {resp.status} {text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


async def login(session: aiohttp.ClientSession, username: str, password: str) -> str:
    flow = await rest(
        session, "POST", "/auth/login_flow",
        json={"client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID},
    )
    res = await rest(
        session, "POST", f"/auth/login_flow/{flow['flow_id']}",
        json={"client_id": CLIENT_ID, "username": username, "password": password},
    )
    tok = await rest(
        session, "POST", "/auth/token",
        data={"grant_type": "authorization_code", "code": res["result"], "client_id": CLIENT_ID},
    )
    return tok["access_token"]


def load_tokens() -> dict[str, str]:
    return json.loads(TOKENS.read_text())


async def wait_for_ha(timeout: float = 180) -> None:
    async with aiohttp.ClientSession() as session:
        for _ in range(int(timeout)):
            try:
                async with session.get(BASE + "/manifest.json") as resp:
                    if resp.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
    raise HAError("Home Assistant did not start")
