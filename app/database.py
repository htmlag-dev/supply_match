"""SQLite-хранилище: поставщики и настройки."""
import sqlite3
import os
import threading
from contextlib import contextmanager
from app.config import DB_PATH, DATA_DIR

EXTRA_FIELDS = ["country", "category", "currency", "unit_price", "moq", "lead_time", "incoterms", "trade_format"]
FIELDS = ["name", "type", "offer", "conditions", "contact", "site", "why", "city", "status", "note"] + EXTRA_FIELDS


class Database:
    def __init__(self, path=DB_PATH, encryption_key=None):
        self._settings_scope = threading.local()
        self.encryption_key = encryption_key
        self.path = str(path)
        DATA_DIR.mkdir(exist_ok=True)
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS suppliers(
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, type TEXT, offer TEXT,
                conditions TEXT, contact TEXT, site TEXT, why TEXT, city TEXT,
                status TEXT DEFAULT 'новый', note TEXT DEFAULT '')""")
            c.execute("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)")
            columns = {row["name"] for row in c.execute("PRAGMA table_info(suppliers)")}
            for field in EXTRA_FIELDS:
                if field not in columns:
                    c.execute(f"ALTER TABLE suppliers ADD COLUMN {field} TEXT DEFAULT ''")
            c.execute("""CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY, plan TEXT NOT NULL, period TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL, email TEXT NOT NULL,
                company TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                idempotency_key TEXT UNIQUE NOT NULL)""")
            c.execute("CREATE TABLE IF NOT EXISTS usage(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)")
            c.execute("""CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                order_id TEXT NOT NULL, merchant TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
                status TEXT NOT NULL, url TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                title TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS purchases(
                id TEXT PRIMARY KEY, supplier_id INTEGER, seller_name TEXT NOT NULL,
                product TEXT NOT NULL, quantity TEXT NOT NULL, unit_price TEXT NOT NULL,
                shipping TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
                buyer_email TEXT NOT NULL, address TEXT NOT NULL, trade_format TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL)""")

    @contextmanager
    def _conn(self):
        if os.environ.get('SUPPLY_MATCH_DATABASE_URL'):
            from app.postgres import connection
            with connection(self.path) as c:
                before=c.total_changes
                yield c
                if c.total_changes!=before:self._settings_scope.values=None
            return
        if self.encryption_key is not None:
            from app.security import encrypted_connection
            with encrypted_connection(self.path, self.encryption_key) as encrypted:
                before=encrypted.total_changes
                yield encrypted
                if encrypted.total_changes!=before:self._settings_scope.values=None
            return
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                before=c.total_changes
                yield c
                if c.total_changes!=before:self._settings_scope.values=None
        finally:
            c.close()

    @contextmanager
    def settings_snapshot(self):
        # Thread/request-local: no stale cache survives a request or crosses accounts.
        previous=(getattr(self._settings_scope,'active',False),getattr(self._settings_scope,'values',None))
        self._settings_scope.active=True;self._settings_scope.values=None
        try:yield self
        finally:self._settings_scope.active=previous[0];self._settings_scope.values=None

    # --- поставщики ---
    def list_suppliers(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM suppliers ORDER BY id DESC")]

    def get_supplier(self, sid):
        with self._conn() as c:
            r = c.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
            return dict(r) if r else None

    def add_supplier(self, data, limit=None):
        vals = [str(data.get(f) or "") for f in FIELDS]
        vals[FIELDS.index("status")] = data.get("status") or "новый"
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT 1 FROM suppliers WHERE name=?", (vals[0],)).fetchone():
                return None  # уже есть
            if limit is not None and c.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0] >= limit:
                raise ValueError("Достигнут лимит базы для вашего тарифа. Откройте «Тарифы и оплата».")
            cur = c.execute(f"INSERT INTO suppliers({','.join(FIELDS)}) VALUES({','.join('?'*len(FIELDS))})", vals)
            return cur.lastrowid

    def update_supplier(self, sid, data):
        keys = [k for k in FIELDS if k in data]
        if not keys:
            return
        with self._conn() as c:
            if "name" in data and c.execute("SELECT 1 FROM suppliers WHERE name=? AND id<>?", (data["name"], sid)).fetchone():
                raise ValueError("Поставщик с таким названием уже есть в базе.")
            c.execute(f"UPDATE suppliers SET {','.join(k+'=?' for k in keys)} WHERE id=?",
                      [data[k] for k in keys] + [sid])

    def delete_supplier(self, sid):
        with self._conn() as c:
            c.execute("DELETE FROM suppliers WHERE id=?", (sid,))

    # --- настройки ---
    def get_setting(self, k, default=""):
        if getattr(self._settings_scope,'active',False):
            if getattr(self._settings_scope,'values',None) is None:
                with self._conn() as c:self._settings_scope.values={r['k']:r['v'] for r in c.execute('SELECT k,v FROM settings')}
            return self._settings_scope.values.get(k,default)
        with self._conn() as c:
            r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default

    def set_setting(self, k, v):
        with self._conn() as c:
            c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def list_orders(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 30")]

    def create_order(self, order):
        with self._conn() as c:
            existing = c.execute("SELECT * FROM orders WHERE idempotency_key=?", (order["idempotency_key"],)).fetchone()
            if existing:
                if any(existing[k] != order[k] for k in ("plan", "period", "email", "company")):
                    raise ValueError("Этот идентификатор уже использован для другой заявки.")
                return dict(existing)
            keys = list(order)
            c.execute(f"INSERT INTO orders({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", list(order.values()))
            return order

    def reserve_usage(self, bucket, limit):
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO usage(bucket,count) VALUES(?,0)", (bucket,))
            if c.execute("SELECT count FROM usage WHERE bucket=?", (bucket,)).fetchone()["count"] >= limit:
                raise ValueError("Лимит ИИ для этого тарифа исчерпан. Откройте «Тарифы и оплата».")
            c.execute("UPDATE usage SET count=count+1 WHERE bucket=?", (bucket,))

    def release_usage(self, bucket):
        with self._conn() as c:
            c.execute("UPDATE usage SET count=MAX(0,count-1) WHERE bucket=?", (bucket,))

    def usage_count(self, bucket):
        with self._conn() as c:
            row = c.execute("SELECT count FROM usage WHERE bucket=?", (bucket,)).fetchone()
            return row["count"] if row else 0

    def add_history(self, kind, title, payload):
        import json
        from datetime import datetime, timezone
        with self._conn() as c:
            c.execute("INSERT INTO history(kind,title,payload,created_at) VALUES(?,?,?,?)",
                (kind, title[:500], json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
            c.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT 100)")

    def list_history(self):
        import json
        with self._conn() as c:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in c.execute("SELECT * FROM history ORDER BY id DESC LIMIT 30")]

    def list_purchases(self):
        with self._conn() as c:
            return [dict(row) for row in c.execute("SELECT * FROM purchases ORDER BY created_at DESC LIMIT 100")]

    def create_purchase(self, purchase):
        with self._conn() as c:
            existing = c.execute("SELECT * FROM purchases WHERE idempotency_key=?", (purchase["idempotency_key"],)).fetchone()
            if existing:
                if any(existing[k] != purchase[k] for k in purchase if k not in ("id", "created_at", "status")):
                    raise ValueError("Этот идентификатор уже используется для другого заказа.")
                return dict(existing)
            keys = list(purchase)
            c.execute(f"INSERT INTO purchases({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", list(purchase.values()))
            return purchase
