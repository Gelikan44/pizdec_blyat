"""SQLite: привязки товаров к услугам и журнал заказов."""
from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS bindings (
    product_id   TEXT PRIMARY KEY,   -- id товара IziMarket
    service_id   INTEGER NOT NULL,   -- id услуги smmway
    per_unit     INTEGER NOT NULL,   -- сколько единиц услуги за 1 купленную штуку
    title        TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    izi_order    TEXT PRIMARY KEY,
    product_id   TEXT,
    link         TEXT,
    quantity     INTEGER,
    smm_order    INTEGER,
    smm_status   TEXT,
    state        TEXT,   -- new | need_link | ordered | delivered | done | error
    note         TEXT,
    created_at   INTEGER,
    updated_at   INTEGER
);
"""


class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    # --- привязки
    def bind(self, product_id: str, service_id: int, per_unit: int, title: str = ""):
        self.c.execute("INSERT OR REPLACE INTO bindings VALUES (?,?,?,?)", (product_id, service_id, per_unit, title))
        self.c.commit()

    def unbind(self, product_id: str) -> bool:
        cur = self.c.execute("DELETE FROM bindings WHERE product_id=?", (product_id,))
        self.c.commit()
        return cur.rowcount > 0

    def binding(self, product_id: str):
        return self.c.execute("SELECT * FROM bindings WHERE product_id=?", (product_id,)).fetchone()

    def bindings(self):
        return self.c.execute("SELECT * FROM bindings").fetchall()

    # --- заказы
    def get(self, izi_order: str):
        return self.c.execute("SELECT * FROM orders WHERE izi_order=?", (izi_order,)).fetchone()

    def insert(self, izi_order: str, product_id: str, quantity: int, state: str = "new"):
        now = int(time.time())
        self.c.execute("INSERT OR IGNORE INTO orders (izi_order, product_id, quantity, state, created_at, updated_at)"
                       " VALUES (?,?,?,?,?,?)", (izi_order, product_id, quantity, state, now, now))
        self.c.commit()

    def update(self, izi_order: str, **fields):
        fields["updated_at"] = int(time.time())
        cols = ", ".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE orders SET {cols} WHERE izi_order=?", (*fields.values(), izi_order))
        self.c.commit()

    def by_state(self, *states: str):
        q = ",".join("?" * len(states))
        return self.c.execute(f"SELECT * FROM orders WHERE state IN ({q})", states).fetchall()

    def last(self, n: int = 10):
        return self.c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (n,)).fetchall()
