"""Клиент API SMM-панели smmway.ru (стандартный протокол SMM-панелей «API v2»).

Документация и ключ: https://smmway.ru/developer
Все запросы — POST с полями key и action, ответ — JSON.
"""
from __future__ import annotations

from typing import Any

import aiohttp


class SmmError(Exception):
    pass


class SmmClient:
    def __init__(self, api_url: str, api_key: str, session: aiohttp.ClientSession):
        self.url, self.key, self.s = api_url, api_key, session

    async def _call(self, action: str, **params) -> Any:
        data = {"key": self.key, "action": action, **{k: v for k, v in params.items() if v is not None}}
        async with self.s.post(self.url, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                raise SmmError(f"smmway: не JSON ответ (HTTP {r.status}): {(await r.text())[:200]}")
        if isinstance(body, dict) and body.get("error"):
            raise SmmError(f"smmway: {body['error']}")
        return body

    async def services(self) -> list[dict]:
        """[{service, name, type, category, rate, min, max, ...}] — rate за 1000 шт."""
        return await self._call("services")

    async def add(self, service: int, link: str, quantity: int, **extra) -> int:
        d = await self._call("add", service=service, link=link, quantity=quantity, **extra)
        if "order" not in d:
            raise SmmError(f"smmway: заказ не создан: {d}")
        return int(d["order"])

    async def status(self, order: int) -> dict:
        """{charge, start_count, status, remains, currency}"""
        return await self._call("status", order=order)

    async def balance(self) -> dict:
        return await self._call("balance")

    async def refill(self, order: int) -> Any:
        return await self._call("refill", order=order)

    async def cancel(self, orders: list[int]) -> Any:
        return await self._call("cancel", orders=",".join(map(str, orders)))
