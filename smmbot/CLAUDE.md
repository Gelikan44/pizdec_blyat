# Контекст проекта (передано из чата с Claude, 05.11.2026)

## Задача владельца
Telegram-бот, связывающий два сайта:
- **izi.market** — маркетплейс, где владелец ПРОДАЁТ товары (SMM-услуги). API продавца: `https://api.izi.market/api/public/v1`,
  авторизация `Authorization: Bearer izp_…`. Документация: https://izi.market/settings/api/docs
  (копия всех методов — в открытом репозитории github.com/fairydarkness/IziMarketHub, папка docs/api).
- **smmway.ru** — SMM-панель, ОТКУДА берутся услуги (подписчики, лайки и т.п.). Стандартный протокол SMM-панелей v2:
  POST на `SMM_API_URL` с полями `key`, `action` (services / add / status / balance / refill / cancel), ответ JSON.
  Документация: https://smmway.ru/developer. **Адрес API `https://smmway.ru/api/v2` не подтверждён** —
  страницу не удалось открыть; `check.py` перебирает варианты и подсказывает верный.

Владелец — не программист, общается по-русски. Объяснять просто, без жаргона.

## Как работает бот (готово и протестировано на имитации API)
1. Каждые `POLL_ORDERS_SEC` (30 с) — `GET /orders?status=paid` на izi.market.
2. Новая сделка → ищется привязка товар→услуга (таблица `bindings`).
   - нет привязки → состояние `unbound`, уведомление админу с командами `/bind` и `/retry`;
   - `GET /orders/{id}` → ссылка из `buyer.inputs` (ключ `LINK_INPUT_KEY` или автопоиск URL / t.me / @username);
   - нет ссылки → `need_link`, просьба покупателю в чат сделки, админу — команда `/link`.
3. `smm.add(service, link, quantity × per_unit)` → состояние `ordered`.
4. `DELIVER_ON=start` — сразу `POST /orders/{id}/deliver` с текстом `MSG_STARTED` (→ `delivered`);
   `complete` — выдача после статуса Completed на smmway.
5. Каждые `POLL_STATUS_SEC` (300 с) — статус на smmway: Completed → сообщение покупателю, `done`;
   Canceled/Partial → `error`, тревога админу.
6. Защита от дублей: состояние `placing` при перезапуске переводится в `error` (ручная проверка); `/retry` не создаёт заказ,
   если `smm_order` уже есть.

## Файлы
- `bot.py` — aiogram 3, команды и фоновые циклы. Команды только для `ADMIN_IDS`.
- `izi_api.py` — клиент izi.market (balance, products, paid_orders, order, deliver, order_chat_id, send_message).
- `smm_api.py` — клиент smmway.
- `db.py` — SQLite `bot.db`: таблицы `bindings(product_id, service_id, per_unit, title)` и `orders`.
- `check.py` — проверка ключей обоих API.
- `install.sh` — venv + служба systemd `smmbot` (`journalctl -u smmbot -f`, `systemctl restart smmbot`).
- `.env` — ключи (BOT_TOKEN, ADMIN_IDS, IZI_API_KEY, SMM_API_KEY, SMM_API_URL и настройки). Не выводить ключи в ответах.

## Команды бота
/start, /balance, /products, /services <поиск>, /bind <ссылка_или_id_товара> <id_услуги> [кол-во_за_1_шт],
/unbind, /bindings, /orders, /order <id>, /link <id_сделки> <ссылка>, /retry <id>, /refill <id>, /check.
Пример товара владельца: https://izi.market/products/3c50a56d-be6c-4cf8-83e1-089a13fb607f (ещё не привязан).

## Что осталось / следующие шаги
1. Запустить `sudo bash install.sh`, убедиться, что `check.py` проходит для обоих API (бот на реальных ключах ещё не запускался).
   Если smmway отвечает не по адресу `/api/v2` — поправить `SMM_API_URL` в `.env`.
2. Ключ izi.market должен иметь права orders:read, orders:write, products:read, balance:read, chats:read, chats:write
   (ключи до 20.10.2026 не умеют выдавать сделки). При 403 public_api_scope_missing — выпустить новый ключ.
3. Помочь владельцу привязать товары (`/services` → `/bind`), проверить, что в товарах есть поле для ссылки покупателя
   (посмотреть `buyer.inputs` реальной сделки и при необходимости задать `LINK_INPUT_KEY`).
4. Проверить на первой реальной сделке формат ответа `GET /chats?order_id=` (в коде ожидается `items[].id`).
5. Ключи и токен были отправлены в чат открытым текстом — после запуска посоветовать перевыпустить их.

## Возможные доработки (владелец не просил, можно предложить)
- Вебхуки izi.market вместо опроса (подпись `X-IziMarket-Signature`: HMAC-SHA256 от `<timestamp>.<body>` ключом `izs_…`).
- Чтение ответа покупателя со ссылкой из чата сделки автоматически (сейчас админ вводит `/link`).
- Автоотключение товаров на izi.market при нулевом балансе smmway.
