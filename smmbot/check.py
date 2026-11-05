"""Проверка ключей: python check.py"""
import asyncio
import os

import aiohttp
from dotenv import load_dotenv

from izi_api import IziClient
from smm_api import SmmClient

load_dotenv()

SMM_URLS = [os.getenv("SMM_API_URL", ""), "https://smmway.ru/api/v2", "https://smmway.ru/api/v1", "https://smmway.ru/api"]


async def main():
    async with aiohttp.ClientSession() as s:
        print("— izi.market")
        try:
            izi = IziClient(os.environ["IZI_API_KEY"], s)
            b = await izi.balance()
            print(f"  OK, доступно {b['available']['amount_minor'] / 100:.2f} {b['currency']}")
            items, _ = await izi.products()
            print(f"  товаров: {len(items)}")
            await izi._req("GET", "/orders", params={"limit": 1})
            print("  доступ к сделкам: OK")
        except Exception as e:
            print("  ОШИБКА:", e)

        print("— smmway")
        for url in dict.fromkeys(u for u in SMM_URLS if u):
            try:
                b = await SmmClient(url, os.environ["SMM_API_KEY"], s).balance()
                print(f"  OK ({url}): баланс {b.get('balance')} {b.get('currency', '')}")
                if url != os.getenv("SMM_API_URL"):
                    print(f"  >>> впишите в .env: SMM_API_URL={url}")
                break
            except Exception as e:
                print(f"  {url}: {e}")

        if not os.getenv("BOT_TOKEN") or not os.getenv("ADMIN_IDS"):
            print("— не заполнены BOT_TOKEN и/или ADMIN_IDS в .env")


asyncio.run(main())
