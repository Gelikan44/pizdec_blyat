"""Telegram-бот: продажа SMM-услуг на izi.market с автовыполнением через smmway.ru.

Схема:
  покупатель оплачивает товар на izi.market
  → бот видит сделку (status=paid), берёт ссылку из полей покупателя
  → создаёт заказ на smmway по привязанной услуге
  → пишет покупателю в чат сделки и выдаёт заказ
  → следит за статусом на smmway и шлёт вам уведомления в Telegram.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

from db import DB
from izi_api import IziClient, IziError
from smm_api import SmmClient, SmmError

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ["ADMIN_IDS"].replace(" ", "").split(",") if x}
IZI_KEY = os.environ["IZI_API_KEY"]
SMM_KEY = os.environ["SMM_API_KEY"]
SMM_URL = os.getenv("SMM_API_URL", "https://smmway.ru/api/v2")
LINK_INPUT_KEY = os.getenv("LINK_INPUT_KEY", "")          # ключ поля со ссылкой у покупателя (если пусто — ищем автоматически)
DELIVER_ON = os.getenv("DELIVER_ON", "start").lower()      # start — выдать сразу после заказа, complete — после выполнения
POLL_ORDERS = int(os.getenv("POLL_ORDERS_SEC", "30"))
POLL_STATUS = int(os.getenv("POLL_STATUS_SEC", "300"))

MSG_STARTED = os.getenv("MSG_STARTED",
    "Спасибо за покупку! Заказ запущен в работу (№{smm}). Ссылка: {link}. Количество: {qty}. "
    "Обычно выполнение начинается в течение часа.")
MSG_DONE = os.getenv("MSG_DONE", "Ваш заказ №{smm} выполнен. Пожалуйста, подтвердите сделку 🙌")
MSG_NEED_LINK = os.getenv("MSG_NEED_LINK",
    "Здравствуйте! Пришлите, пожалуйста, ссылку, куда нужно выполнить услугу.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smmbot")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
db = DB(os.getenv("DB_PATH", "bot.db"))
izi: IziClient
smm: SmmClient

URL_RE = re.compile(r"(https?://\S+|t\.me/\S+|@[A-Za-z0-9_]{4,})")

KB = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="📦 Заказы")],
    [KeyboardButton(text="🔗 Привязки"), KeyboardButton(text="🛒 Мои товары")],
])


def e(x) -> str:
    return html.escape(str(x))


def rub(m: dict) -> str:
    return f"{m['amount_minor'] / 100:.2f} {m.get('currency', 'RUB')}"


async def notify(text: str):
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(uid, text, disable_web_page_preview=True)
        except Exception as ex:
            log.warning("не смог написать %s: %s", uid, ex)


async def buyer_msg(izi_order: str, text: str):
    try:
        chat = await izi.order_chat_id(izi_order)
        if chat:
            await izi.send_message(chat, text)
    except IziError as ex:
        log.warning("сообщение в чат сделки %s не ушло: %s", izi_order, ex)


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def product_id(arg: str) -> str:
    """Принимает id товара или ссылку вида https://izi.market/products/<id>."""
    m = UUID_RE.search(arg)
    return m.group(0).lower() if m else arg.strip()


def find_link(inputs: dict | None) -> str | None:
    if not inputs:
        return None
    if LINK_INPUT_KEY and inputs.get(LINK_INPUT_KEY):
        return str(inputs[LINK_INPUT_KEY]).strip()
    for v in inputs.values():
        if v and URL_RE.search(str(v)):
            return URL_RE.search(str(v)).group(0)
    return None


# ------------------------------------------------------------------ логика заказов

async def place_order(izi_order: str):
    row = db.get(izi_order)
    b = db.binding(row["product_id"])
    qty = row["quantity"] * b["per_unit"]
    db.update(izi_order, state="placing")
    try:
        smm_id = await smm.add(b["service_id"], row["link"], qty)
    except (SmmError, aiohttp.ClientError, asyncio.TimeoutError) as ex:
        db.update(izi_order, state="error", note=str(ex))
        await notify(f"❌ Не удалось создать заказ на smmway для сделки <code>{e(izi_order)}</code>\n{e(ex)}\n"
                     f"Повторить: <code>/retry {e(izi_order)}</code>")
        return
    db.update(izi_order, state="ordered", smm_order=smm_id, smm_status="Pending")
    text = MSG_STARTED.format(smm=smm_id, link=row["link"], qty=qty)
    if DELIVER_ON == "start":
        try:
            await izi.deliver(izi_order, text)
            db.update(izi_order, state="delivered")
        except IziError as ex:
            await notify(f"⚠️ Заказ на smmway создан (№{smm_id}), но выдать сделку не вышло: {e(ex)}")
    else:
        await buyer_msg(izi_order, text)
    await notify(f"✅ Новая продажа → заказ smmway №{smm_id}\n"
                 f"Сделка: <code>{e(izi_order)}</code>\nУслуга: {b['service_id']} · {qty} шт.\nСсылка: {e(row['link'])}")


async def check_new_orders():
    for o in await izi.paid_orders():
        oid = o["id"]
        if db.get(oid):
            continue
        b = db.binding(o["product_id"])
        db.insert(oid, o["product_id"], int(o.get("quantity") or 1), state="new")
        if not b:
            db.update(oid, state="unbound")
            await notify(f"🔔 Оплачена сделка по товару без привязки к услуге: «{e(o.get('product_title'))}»\n"
                         f"Товар: <code>{e(o['product_id'])}</code> · сделка <code>{e(oid)}</code>\n"
                         f"Привязать: <code>/bind {e(o['product_id'])} ID_услуги</code>, затем <code>/retry {e(oid)}</code>")
            continue
        full = await izi.order(oid)
        link = find_link((full.get("buyer") or {}).get("inputs"))
        if not link:
            db.update(oid, state="need_link")
            await buyer_msg(oid, MSG_NEED_LINK)
            await notify(f"🔗 В сделке <code>{e(oid)}</code> нет ссылки от покупателя. Попросил её в чате.\n"
                         f"Когда пришлёт: <code>/link {e(oid)} ссылка</code>")
            continue
        db.update(oid, link=link)
        await place_order(oid)


async def check_statuses():
    for row in db.by_state("ordered", "delivered"):
        try:
            st = await smm.status(row["smm_order"])
        except (SmmError, aiohttp.ClientError, asyncio.TimeoutError) as ex:
            log.warning("статус %s: %s", row["smm_order"], ex)
            continue
        status = st.get("status", "")
        if status == row["smm_status"]:
            continue
        db.update(row["izi_order"], smm_status=status)
        low = status.lower()
        if low == "completed":
            if row["state"] == "ordered":
                try:
                    await izi.deliver(row["izi_order"], MSG_DONE.format(smm=row["smm_order"]))
                except IziError as ex:
                    await notify(f"⚠️ Выполнен заказ №{row['smm_order']}, но выдать сделку не вышло: {e(ex)}")
                    continue
            else:
                await buyer_msg(row["izi_order"], MSG_DONE.format(smm=row["smm_order"]))
            db.update(row["izi_order"], state="done")
            await notify(f"🏁 Заказ smmway №{row['smm_order']} выполнен (сделка <code>{e(row['izi_order'])}</code>)")
        elif low in ("canceled", "cancelled", "partial", "fail", "error"):
            db.update(row["izi_order"], state="error", note=status)
            await notify(f"🚨 Заказ smmway №{row['smm_order']}: статус <b>{e(status)}</b>, остаток {e(st.get('remains'))}\n"
                         f"Сделка <code>{e(row['izi_order'])}</code> — проверьте вручную.")


async def loop(fn, every: int):
    while True:
        try:
            await fn()
        except Exception as ex:
            log.exception("%s: %s", fn.__name__, ex)
        await asyncio.sleep(every)


# ------------------------------------------------------------------ команды

@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("start", "help"))
async def cmd_start(m: Message):
    await m.answer(
        "<b>Бот izi.market × smmway</b>\n\n"
        "/balance — балансы на обоих сайтах\n"
        "/products — мои товары на izi.market (с id)\n"
        "/services <i>поиск</i> — услуги smmway\n"
        "/bind <i>id_товара id_услуги [кол-во_за_1_шт]</i> — привязать товар к услуге\n"
        "/unbind <i>id_товара</i> · /bindings — привязки\n"
        "/orders — последние заказы\n"
        "/order <i>id_сделки</i> — подробно + статус на smmway\n"
        "/link <i>id_сделки ссылка</i> — задать ссылку и запустить заказ\n"
        "/retry <i>id_сделки</i> — повторить запуск заказа\n"
        "/refill <i>id_сделки</i> — запросить докрутку на smmway\n"
        "/check — проверить новые сделки прямо сейчас", reply_markup=KB)


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("balance"))
@dp.message(F.from_user.id.in_(ADMIN_IDS), F.text == "💰 Баланс")
async def cmd_balance(m: Message):
    lines = []
    try:
        b = await izi.balance()
        lines.append(f"izi.market: <b>{rub(b['available'])}</b> доступно (всего {rub(b['balance'])})")
    except IziError as ex:
        lines.append(f"izi.market: ошибка — {e(ex)}")
    try:
        s = await smm.balance()
        lines.append(f"smmway: <b>{e(s.get('balance'))} {e(s.get('currency', ''))}</b>")
    except Exception as ex:
        lines.append(f"smmway: ошибка — {e(ex)}")
    await m.answer("\n".join(lines))


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("products"))
@dp.message(F.from_user.id.in_(ADMIN_IDS), F.text == "🛒 Мои товары")
async def cmd_products(m: Message):
    items, _ = await izi.products()
    if not items:
        return await m.answer("Товаров нет.")
    bound = {r["product_id"] for r in db.bindings()}
    out = [f"{'🔗' if p['id'] in bound else '▫️'} {e(p['title'][:60])} — {rub(p['price'])}\n<code>{p['id']}</code>"
           for p in items[:30]]
    await m.answer("\n\n".join(out) + "\n\n🔗 — привязан к услуге")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("services"))
async def cmd_services(m: Message, command: CommandObject):
    q = (command.args or "").lower()
    items = await smm.services()
    found = [s for s in items if q in f"{s.get('name')} {s.get('category')}".lower()][:25]
    if not found:
        return await m.answer("Ничего не нашёл. Пример: /services telegram подписчики")
    await m.answer("\n".join(f"<code>{s['service']}</code> · {e(s['name'][:70])} · {e(s['rate'])}/1000 · "
                             f"{s.get('min')}–{s.get('max')}" for s in found))


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("bind"))
async def cmd_bind(m: Message, command: CommandObject):
    a = (command.args or "").split()
    if len(a) < 2 or not a[1].isdigit():
        return await m.answer("Формат: /bind id_товара id_услуги [кол-во_за_1_шт]\nНапр.: /bind 0e33…111 1234 1000")
    per_unit = int(a[2]) if len(a) > 2 and a[2].isdigit() else 1
    pid = product_id(a[0])
    svc = next((s for s in await smm.services() if str(s.get("service")) == a[1]), None)
    if not svc:
        return await m.answer(f"Услуги {a[1]} нет на smmway. Найдите нужную: /services поиск")
    db.bind(pid, int(a[1]), per_unit, svc.get("name", ""))
    warn = ""
    if per_unit < int(svc.get("min") or 0):
        warn = f"\n⚠️ Минимум услуги — {svc['min']} шт., а за 1 покупку будет {per_unit}. Поднимите минимальное кол-во в товаре."
    await m.answer(f"Готово: товар <code>{e(pid)}</code>\n→ услуга {a[1]} «{e(svc.get('name', '')[:80])}»\n"
                   f"{per_unit} шт. за каждую купленную единицу (цена smmway {e(svc.get('rate'))} за 1000).{warn}")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("unbind"))
async def cmd_unbind(m: Message, command: CommandObject):
    ok = db.unbind(product_id(command.args or ""))
    await m.answer("Привязка удалена." if ok else "Такой привязки нет.")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("bindings"))
@dp.message(F.from_user.id.in_(ADMIN_IDS), F.text == "🔗 Привязки")
async def cmd_bindings(m: Message):
    rows = db.bindings()
    if not rows:
        return await m.answer("Привязок пока нет. Смотрите /products и /services, затем /bind.")
    await m.answer("\n\n".join(f"<code>{r['product_id']}</code>\n→ услуга {r['service_id']} × {r['per_unit']}"
                               f"{' · ' + e(r['title'][:50]) if r['title'] else ''}" for r in rows))


STATE_RU = {"new": "новая", "unbound": "нет привязки", "need_link": "ждёт ссылку", "placing": "создаётся",
            "ordered": "в работе", "delivered": "выдана, в работе", "done": "выполнено", "error": "ошибка"}


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("orders"))
@dp.message(F.from_user.id.in_(ADMIN_IDS), F.text == "📦 Заказы")
async def cmd_orders(m: Message):
    rows = db.last(15)
    if not rows:
        return await m.answer("Заказов пока не было.")
    await m.answer("\n".join(f"• <code>{r['izi_order'][:8]}…</code> {STATE_RU.get(r['state'], r['state'])}"
                             f"{' · smm №' + str(r['smm_order']) if r['smm_order'] else ''}"
                             f"{' · ' + e(r['smm_status']) if r['smm_status'] else ''}" for r in rows)
                   + "\n\nПолный id: /order <i>начало id</i>")


def find_order(prefix: str):
    prefix = prefix.strip()
    if not prefix:
        return None
    r = db.get(prefix)
    if r:
        return r
    rows = db.c.execute("SELECT * FROM orders WHERE izi_order LIKE ?", (prefix + "%",)).fetchall()
    return rows[0] if len(rows) == 1 else None


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("order"))
async def cmd_order(m: Message, command: CommandObject):
    r = find_order(command.args or "")
    if not r:
        return await m.answer("Сделка не найдена.")
    text = (f"Сделка <code>{r['izi_order']}</code>\nТовар <code>{r['product_id']}</code>\n"
            f"Состояние: {STATE_RU.get(r['state'], r['state'])}\nСсылка: {e(r['link'])}\nКол-во: {r['quantity']}\n"
            f"smmway №{r['smm_order'] or '—'}")
    if r["note"]:
        text += f"\nЗаметка: {e(r['note'])}"
    if r["smm_order"]:
        try:
            st = await smm.status(r["smm_order"])
            text += (f"\n\nНа smmway: <b>{e(st.get('status'))}</b>, старт {e(st.get('start_count'))}, "
                     f"осталось {e(st.get('remains'))}, списано {e(st.get('charge'))}")
        except Exception as ex:
            text += f"\n\nСтатус smmway: ошибка {e(ex)}"
    await m.answer(text, disable_web_page_preview=True)


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("link"))
async def cmd_link(m: Message, command: CommandObject):
    a = (command.args or "").split(maxsplit=1)
    r = find_order(a[0]) if a else None
    if not r or len(a) < 2:
        return await m.answer("Формат: /link id_сделки ссылка")
    if r["state"] not in ("need_link", "error", "unbound", "new"):
        return await m.answer("По этой сделке заказ уже создан.")
    if not db.binding(r["product_id"]):
        return await m.answer("Сначала привяжите товар: /bind")
    db.update(r["izi_order"], link=a[1].strip())
    await place_order(r["izi_order"])


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("retry"))
async def cmd_retry(m: Message, command: CommandObject):
    r = find_order(command.args or "")
    if not r:
        return await m.answer("Сделка не найдена.")
    if r["smm_order"]:
        return await m.answer(f"На smmway уже есть заказ №{r['smm_order']} — повтор создал бы дубль.")
    if not db.binding(r["product_id"]):
        return await m.answer("Товар не привязан к услуге: /bind")
    if not r["link"]:
        full = await izi.order(r["izi_order"])
        link = find_link((full.get("buyer") or {}).get("inputs"))
        if not link:
            db.update(r["izi_order"], state="need_link")
            return await m.answer("Ссылки нет. Задайте: /link id_сделки ссылка")
        db.update(r["izi_order"], link=link)
    await place_order(r["izi_order"])


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("refill"))
async def cmd_refill(m: Message, command: CommandObject):
    r = find_order(command.args or "")
    if not r or not r["smm_order"]:
        return await m.answer("Сделка или заказ smmway не найдены.")
    try:
        await m.answer(f"Ответ smmway: <code>{e(await smm.refill(r['smm_order']))}</code>")
    except Exception as ex:
        await m.answer(f"Ошибка: {e(ex)}")


@dp.message(F.from_user.id.in_(ADMIN_IDS), Command("check"))
async def cmd_check(m: Message):
    await check_new_orders()
    await check_statuses()
    await m.answer("Проверил.")


@dp.message()
async def other(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        await m.answer(f"Это служебный бот магазина. Ваш Telegram ID: <code>{m.from_user.id}</code>")


async def main():
    global izi, smm
    async with aiohttp.ClientSession() as session:
        izi = IziClient(IZI_KEY, session)
        smm = SmmClient(SMM_URL, SMM_KEY, session)
        # прерванные запуски — на ручную проверку, чтобы не создать дубль
        for r in db.by_state("placing"):
            db.update(r["izi_order"], state="error", note="бот перезапустился во время создания заказа")
        asyncio.create_task(loop(check_new_orders, POLL_ORDERS))
        asyncio.create_task(loop(check_statuses, POLL_STATUS))
        await notify("🤖 Бот запущен")
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
