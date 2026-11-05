"""Минимальный асинхронный клиент API продавца IziMarket.

Документация: https://izi.market/settings/api/docs
Ключ (izp_…) выпускается в кабинете: Настройки → API.
Нужные права: orders:read, orders:write, products:read, balance:read, chats:write.
"""
from __future__ import annotations

import uuid
from typing import Any

import aiohttp

BASE_URL = "https://api.izi.market/api/public/v1"


class IziError(Exception):
    def __init__(self, status: int, code: str | None, message: str):
        super().__init__(f"IziMarket {status} {code or ''}: {message}")
        self.status, self.code = status, code


class IziClient:
    def __init__(self, api_key: str, session: aiohttp.ClientSession):
        self.s = session
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Accept-Language": "ru",
        }

    async def _req(self, method: str, path: str, *, params=None, json=None, idem: str | None = None) -> Any:
        headers = dict(self.headers)
        if idem:
            headers["Idempotency-Key"] = idem
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        async with self.s.request(method, BASE_URL + path, params=params, json=json,
                                  headers=headers, timeout=aiohttp.ClientTimeout(total=25)) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                body = None
            if r.ok and isinstance(body, dict) and body.get("status", True):
                return body.get("data")
            data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}
            raise IziError(r.status, data.get("code"), data.get("message") or str(body)[:200])

    # --- аккаунт
    async def balance(self) -> dict:
        return await self._req("GET", "/balance")

    # --- товары
    async def products(self, page: int = 1, search: str | None = None) -> tuple[list[dict], bool]:
        d = await self._req("GET", "/products", params={"page": page, "search": search})
        return d["items"], bool(d.get("has_next_page"))

    # --- сделки
    async def paid_orders(self) -> list[dict]:
        """Все сделки, ожидающие выдачи (status=paid)."""
        out, cursor = [], None
        while True:
            d = await self._req("GET", "/orders", params={"status": "paid", "limit": 50, "cursor": cursor})
            out += d["items"]
            cursor = d.get("next_cursor")
            if not d.get("has_next_page") or not cursor:
                return out

    async def order(self, order_id: str) -> dict:
        return await self._req("GET", f"/orders/{order_id}")

    async def deliver(self, order_id: str, content: str) -> dict:
        return await self._req("POST", f"/orders/{order_id}/deliver", json={"content": content[:2000]},
                               idem=f"deliver-{order_id}")

    # --- чат сделки
    async def order_chat_id(self, order_id: str) -> str | None:
        d = await self._req("GET", "/chats", params={"order_id": order_id})
        items = d.get("items", []) if isinstance(d, dict) else d
        return items[0]["id"] if items else None

    async def send_message(self, chat_id: str, text: str) -> dict:
        return await self._req("POST", f"/chats/{chat_id}/messages",
                               json={"text": text[:4000], "client_message_id": str(uuid.uuid4())},
                               idem="msg-" + uuid.uuid4().hex)
