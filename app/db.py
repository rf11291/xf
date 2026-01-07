from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
import json
import datetime as dt

# =========================
# Schema v4:
# - products: 产品库（一个产品可被多个客户订阅）
# - subscriptions: 客户订阅（到期时间可修改，用于续费）
# =========================

SCHEMA_V4 = r'''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  content TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  expires_at TEXT NOT NULL,  -- YYYY-MM-DD
  note TEXT,                 -- 客户专属备注
  created_at TEXT NOT NULL,
  FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminder_sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER NOT NULL,
  days_before INTEGER NOT NULL,
  sent_at TEXT NOT NULL,
  UNIQUE(subscription_id, days_before),
  FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS reminder_daily_sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id INTEGER NOT NULL,
  sent_date TEXT NOT NULL,  -- YYYY-MM-DD (local date)
  sent_at TEXT NOT NULL,
  UNIQUE(subscription_id, sent_date),
  FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
CREATE INDEX IF NOT EXISTS idx_subscriptions_expires ON subscriptions(expires_at);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_product ON subscriptions(product_id);
CREATE INDEX IF NOT EXISTS idx_daily_sends_date ON reminder_daily_sends(sent_date);
'''

DEFAULT_RULES = [30, 7, 1, 0]
DEFAULT_TEMPLATE = {
    "subject": "【续费提醒】{{ product.name }} 将在 {{ product.expires_at }} 到期",
    "html": '''<p>Hi {{ customer.name or customer.email }},</p>
<p>你的产品 <b>{{ product.name }}</b> 将在 <b>{{ product.expires_at }}</b> 到期。</p>
<p>距离到期还剩 <b>{{ days_left }}</b> 天。</p>
{% if product.content %}<p>备注：{{ product.content }}</p>{% endif %}
<hr/>
<p>如需继续续费使用，请联系 <a href="{{ contact_url }}" target="_blank" rel="noopener noreferrer">{{ contact_name }}</a>。</p>
<p>— {{ company }}</p>
'''
}

DEFAULT_RENEWAL_CONFIRM_TEMPLATE = {
    "subject": "【续费成功】{{ product.name }} 已续费至 {{ new_expires_at }}",
    "html": '''<p>Hi {{ customer.name or customer.email }},</p>
<p>你的产品 <b>{{ product.name }}</b> 已续费成功 ✅</p>
<p>原到期日：<b>{{ old_expires_at }}</b></p>
<p>新到期日：<b>{{ new_expires_at }}</b></p>
{% if product.content %}<p>产品信息：{{ product.content }}</p>{% endif %}
<hr/>
<p>— {{ company }}</p>
'''
}


def _utc_now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None

def _table_cols(conn: sqlite3.Connection, name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
    return {str(r[1]) for r in rows}

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init(self) -> None:
        with self._conn() as conn:
            self._maybe_migrate_legacy(conn)
            conn.executescript(SCHEMA_V4)

            if self.get_setting("reminder_rules", conn=conn) is None:
                self.set_setting("reminder_rules", json.dumps(DEFAULT_RULES, ensure_ascii=False), conn=conn)
            if self.get_setting("email_template", conn=conn) is None:
                self.set_setting("email_template", json.dumps(DEFAULT_TEMPLATE, ensure_ascii=False), conn=conn)
            if self.get_setting("renewal_confirm_template", conn=conn) is None:
                self.set_setting("renewal_confirm_template", json.dumps(DEFAULT_RENEWAL_CONFIRM_TEMPLATE, ensure_ascii=False), conn=conn)

            conn.commit()

    def _maybe_migrate_legacy(self, conn: sqlite3.Connection) -> None:
        # Legacy v3 had table: products(customer_id, name, content, expires_at, ...)
        if not _table_exists(conn, "products"):
            return
        if _table_exists(conn, "subscriptions"):
            return

        cols = _table_cols(conn, "products")
        legacy_like = {"customer_id", "expires_at", "name"}.issubset(cols)
        if not legacy_like:
            return

        # Rename legacy tables to keep backup
        if not _table_exists(conn, "legacy_products"):
            conn.execute("ALTER TABLE products RENAME TO legacy_products;")
        else:
            conn.execute("DROP TABLE products;")

        if _table_exists(conn, "reminder_sends") and not _table_exists(conn, "legacy_reminder_sends"):
            conn.execute("ALTER TABLE reminder_sends RENAME TO legacy_reminder_sends;")

        # Create new tables
        conn.executescript(SCHEMA_V4)

        legacy_rows = conn.execute("SELECT * FROM legacy_products ORDER BY id ASC").fetchall()
        legacy_to_sub: dict[int, int] = {}

        for r in legacy_rows:
            legacy_id = int(r["id"])
            customer_id = int(r["customer_id"])
            name = str(r["name"])
            note = r["content"] if "content" in r.keys() else None
            expires_at = str(r["expires_at"])

            product_id = self.upsert_product(name=name, content=None, conn=conn)
            sub_id = self.add_subscription(
                customer_id=customer_id,
                product_id=product_id,
                expires_at=expires_at,
                note=note,
                conn=conn,
            )
            legacy_to_sub[legacy_id] = sub_id

        if _table_exists(conn, "legacy_reminder_sends"):
            rows = conn.execute("SELECT * FROM legacy_reminder_sends").fetchall()
            for rr in rows:
                legacy_pid = int(rr["product_id"])
                sub_id = legacy_to_sub.get(legacy_pid)
                if not sub_id:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO reminder_sends(subscription_id, days_before, sent_at) VALUES(?,?,?)",
                    (sub_id, int(rr["days_before"]), str(rr["sent_at"])),
                )

        conn.commit()

    # ---------------- settings ----------------
    def get_setting(self, key: str, conn: sqlite3.Connection | None = None) -> str | None:
        close = False
        if conn is None:
            conn = self._conn()
            close = True
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return None if row is None else str(row["value"])
        finally:
            if close:
                conn.close()

    def set_setting(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        close = False
        if conn is None:
            conn = self._conn()
            close = True
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            if close:
                conn.close()

    # ---------------- customers ----------------
    def upsert_customer(self, email: str, name: str | None) -> int:
        email = email.strip().lower()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO customers(email, name, created_at) VALUES(?,?,?) "
                "ON CONFLICT(email) DO UPDATE SET name=excluded.name",
                (email, name, _utc_now_iso()),
            )
            conn.commit()
            row = conn.execute("SELECT id FROM customers WHERE email=?", (email,)).fetchone()
            return int(row["id"])

    def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
            return None if row is None else dict(row)

    def list_customers(self, search: str | None = None, offset: int = 0, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                rows = conn.execute(
                    "SELECT * FROM customers WHERE lower(email) LIKE ? OR lower(name) LIKE ? "
                    "ORDER BY id DESC LIMIT ? OFFSET ?",
                    (q, q, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM customers ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def count_customers(self, search: str | None = None) -> int:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                row = conn.execute(
                    "SELECT COUNT(1) AS c FROM customers WHERE lower(email) LIKE ? OR lower(name) LIKE ?",
                    (q, q),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(1) AS c FROM customers").fetchone()
            return int(row["c"])

    def delete_customer(self, customer_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
            conn.commit()

    def update_customer(self, customer_id: int, email: str, name: str | None) -> bool:
        email = email.strip().lower()
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE customers SET email=?, name=? WHERE id=?",
                    (email, name, customer_id),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # ---------------- product catalog ----------------
    def upsert_product(self, name: str, content: str | None, conn: sqlite3.Connection | None = None) -> int:
        close = False
        if conn is None:
            conn = self._conn()
            close = True
        try:
            name = name.strip()
            conn.execute(
                "INSERT INTO products(name, content, created_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET content=COALESCE(excluded.content, products.content)",
                (name, content, _utc_now_iso()),
            )
            row = conn.execute("SELECT id FROM products WHERE name=?", (name,)).fetchone()
            conn.commit()
            return int(row["id"])
        finally:
            if close:
                conn.close()

    def add_product(self, name: str, content: str | None) -> int:
        return self.upsert_product(name=name, content=content)

    def get_product(self, product_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            return None if row is None else dict(row)

    def list_products(self, search: str | None = None, offset: int = 0, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                rows = conn.execute(
                    "SELECT * FROM products WHERE lower(name) LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (q, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM products ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def count_products(self, search: str | None = None) -> int:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                row = conn.execute(
                    "SELECT COUNT(1) AS c FROM products WHERE lower(name) LIKE ?",
                    (q,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(1) AS c FROM products").fetchone()
            return int(row["c"])

    def count_subscriptions_for_product(self, product_id: int) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS c FROM subscriptions WHERE product_id=?",
                (product_id,),
            ).fetchone()
            return int(row["c"])

    def delete_product(self, product_id: int) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(1) AS c FROM subscriptions WHERE product_id=?", (product_id,)).fetchone()
            if int(row["c"]) > 0:
                return False
            conn.execute("DELETE FROM products WHERE id=?", (product_id,))
            conn.commit()
            return True

    def update_product(self, product_id: int, name: str, content: str | None) -> bool:
        name = name.strip()
        with self._conn() as conn:
            try:
                conn.execute(
                    "UPDATE products SET name=?, content=? WHERE id=?",
                    (name, content, product_id),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # ---------------- subscriptions ----------------
    def add_subscription(
        self,
        customer_id: int,
        product_id: int,
        expires_at: str,
        note: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        close = False
        if conn is None:
            conn = self._conn()
            close = True
        try:
            cur = conn.execute(
                "INSERT INTO subscriptions(customer_id, product_id, expires_at, note, created_at) VALUES(?,?,?,?,?)",
                (customer_id, product_id, expires_at, note, _utc_now_iso()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            if close:
                conn.close()

    def update_subscription_expires(self, subscription_id: int, new_expires_at: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE subscriptions SET expires_at=? WHERE id=?", (new_expires_at, subscription_id))
            conn.commit()

    def update_subscription(self, subscription_id: int, new_expires_at: str, note: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE subscriptions SET expires_at=?, note=? WHERE id=?",
                (new_expires_at, note, subscription_id),
            )
            conn.commit()

    def delete_subscription(self, subscription_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM subscriptions WHERE id=?", (subscription_id,))
            conn.commit()

    def get_subscription_detail(self, subscription_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                '''
                SELECT s.*,
                       c.email AS customer_email, c.name AS customer_name,
                       p.name AS product_name, p.content AS product_content
                FROM subscriptions s
                JOIN customers c ON c.id=s.customer_id
                JOIN products p ON p.id=s.product_id
                WHERE s.id=?
                ''',
                (subscription_id,),
            ).fetchone()
            return None if row is None else dict(row)

    def list_subscriptions_by_customer(self, customer_id: int, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                '''
                SELECT s.*, p.name AS product_name
                FROM subscriptions s
                JOIN products p ON p.id=s.product_id
                WHERE s.customer_id=?
                ORDER BY s.expires_at ASC
                LIMIT ? OFFSET ?
                ''',
                (customer_id, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all_subscription_details(
        self,
        search: str | None = None,
        offset: int = 0,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                rows = conn.execute(
                    '''
                    SELECT s.*,
                           c.email AS customer_email, c.name AS customer_name,
                           p.name AS product_name, p.content AS product_content
                    FROM subscriptions s
                    JOIN customers c ON c.id=s.customer_id
                    JOIN products p ON p.id=s.product_id
                    WHERE lower(c.email) LIKE ? OR lower(c.name) LIKE ? OR lower(p.name) LIKE ?
                    ORDER BY s.expires_at ASC
                    LIMIT ? OFFSET ?
                    ''',
                    (q, q, q, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    '''
                    SELECT s.*,
                           c.email AS customer_email, c.name AS customer_name,
                           p.name AS product_name, p.content AS product_content
                    FROM subscriptions s
                    JOIN customers c ON c.id=s.customer_id
                    JOIN products p ON p.id=s.product_id
                    ORDER BY s.expires_at ASC
                    LIMIT ? OFFSET ?
                    ''',
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def count_subscriptions(self, search: str | None = None) -> int:
        with self._conn() as conn:
            if search:
                q = f"%{search.lower()}%"
                row = conn.execute(
                    '''
                    SELECT COUNT(1) AS c
                    FROM subscriptions s
                    JOIN customers c ON c.id=s.customer_id
                    JOIN products p ON p.id=s.product_id
                    WHERE lower(c.email) LIKE ? OR lower(c.name) LIKE ? OR lower(p.name) LIKE ?
                    ''',
                    (q, q, q),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(1) AS c FROM subscriptions").fetchone()
            return int(row["c"])

    def list_subscriptions_expiring_within(self, days: int, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        today = dt.date.today()
        end_date = today + dt.timedelta(days=days)
        with self._conn() as conn:
            rows = conn.execute(
                '''
                SELECT s.*,
                       c.email AS customer_email, c.name AS customer_name,
                       p.name AS product_name, p.content AS product_content
                FROM subscriptions s
                JOIN customers c ON c.id=s.customer_id
                JOIN products p ON p.id=s.product_id
                WHERE s.expires_at >= ? AND s.expires_at <= ?
                ORDER BY s.expires_at ASC
                LIMIT ? OFFSET ?
                ''',
                (today.isoformat(), end_date.isoformat(), limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_reminder_daily_logs(self, offset: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                '''
                SELECT r.sent_date, r.sent_at, r.subscription_id,
                       c.email AS customer_email, c.name AS customer_name,
                       p.name AS product_name,
                       s.expires_at
                FROM reminder_daily_sends r
                JOIN subscriptions s ON s.id=r.subscription_id
                JOIN customers c ON c.id=s.customer_id
                JOIN products p ON p.id=s.product_id
                ORDER BY r.sent_at DESC
                LIMIT ? OFFSET ?
                ''',
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_reminder_daily_logs(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(1) AS c FROM reminder_daily_sends").fetchone()
            return int(row["c"])

    # ---------------- reminder sends ----------------
    def was_sent(self, subscription_id: int, days_before: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM reminder_sends WHERE subscription_id=? AND days_before=?",
                (subscription_id, days_before),
            ).fetchone()
            return row is not None

    def mark_sent(self, subscription_id: int, days_before: int, sent_at: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO reminder_sends(subscription_id, days_before, sent_at) VALUES(?,?,?)",
                (subscription_id, days_before, sent_at or _utc_now_iso()),
            )
            conn.commit()



    # ---------------- daily send guard ----------------
    def was_sent_on(self, subscription_id: int, sent_date: str) -> bool:
        """sent_date: YYYY-MM-DD (local date)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM reminder_daily_sends WHERE subscription_id=? AND sent_date=?",
                (subscription_id, sent_date),
            ).fetchone()
            return row is not None

    def mark_sent_on(self, subscription_id: int, sent_date: str, sent_at: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO reminder_daily_sends(subscription_id, sent_date, sent_at) VALUES(?,?,?)",
                (subscription_id, sent_date, sent_at or _utc_now_iso()),
            )
            conn.commit()
