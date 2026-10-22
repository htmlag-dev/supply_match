"""ЮKassa redirect checkout. Подтверждение всегда запрашивается сервером у провайдера."""
import base64
import calendar
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


class PaymentError(ValueError):
    pass


class YooKassaGateway:
    def __init__(self, db):
        self.db = db

    def credentials(self, merchant):
        prefix = "SUPPLY_MATCH_" + merchant.upper()
        return os.environ.get(prefix + "_SHOP_ID", ""), os.environ.get(prefix + "_SECRET", "")

    def enabled(self, merchant):
        shop, secret = self.credentials(merchant)
        return bool(shop and secret and os.environ.get("SUPPLY_MATCH_PAYMENT_RETURN_URL", "").startswith("https://"))

    def _request(self, merchant, path, payload=None, idempotency_key=None):
        shop, secret = self.credentials(merchant)
        headers = {"Authorization": "Basic " + base64.b64encode((shop + ":" + secret).encode()).decode(),
                   "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotence-Key"] = idempotency_key
        request = urllib.request.Request("https://api.yookassa.ru/v3/" + path,
            data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise PaymentError("Платёжный сервис не ответил. Заказ сохранён; повторите оформление с теми же данными.") from None
        if not isinstance(result, dict):
            raise PaymentError("Платёжный сервис вернул неверный ответ.")
        return result

    def checkout(self, kind, order, merchant):
        with self.db._conn() as conn:
            existing = conn.execute("SELECT * FROM payments WHERE kind=? AND order_id=?", (kind, order["id"])).fetchone()
        if existing:
            return {"checkout_url": existing["url"] if existing["status"] == "pending" else None,
                    "payment_enabled": existing["status"] == "pending",
                    "message": "Платёж уже создан. Продолжите оплату по ссылке." if existing["status"] == "pending" else "Этот заказ уже оплачен."}
        if order["currency"] != "RUB" or not self.enabled(merchant):
            return None
        if kind == "subscription" and order["plan"] != "pro":
            return None  # Командная версия ещё не запущена.
        amount = format(Decimal(order["amount"]) / 100, ".2f") if kind == "subscription" else order["amount"]
        description = "Supply Match Профи" if kind == "subscription" else order["product"][:100]
        result = self._request(merchant, "payments", {"amount": {"value": amount, "currency": "RUB"},
            "capture": True, "confirmation": {"type": "redirect", "return_url": os.environ["SUPPLY_MATCH_PAYMENT_RETURN_URL"]},
            "description": description, "metadata": {"order_id": order["id"], "kind": kind}}, order["id"])
        payment_id = str(result.get("id", ""))
        confirmation = result.get("confirmation", {}).get("confirmation_url", "")
        parsed = urlsplit(confirmation)
        if not re.fullmatch(r"[A-Za-z0-9-]{16,64}", payment_id) or parsed.scheme != "https" or not parsed.netloc:
            raise PaymentError("Платёжный сервис не вернул корректную ссылку оплаты.")
        with self.db._conn() as conn:
            conn.execute("""INSERT INTO payments(id,kind,order_id,merchant,amount,currency,status,url)
                VALUES(?,?,?,?,?,?,'pending',?) ON CONFLICT(id) DO NOTHING""",
                (payment_id, kind, order["id"], merchant, amount, "RUB", confirmation))
        return {"checkout_url": confirmation, "payment_enabled": True,
                "message": "Заказ сохранён. Завершите оплату на защищённой странице ЮKassa. Статус обновится после серверного подтверждения."}

    def webhook(self, data):
        payment_id = str(data.get("object", {}).get("id", "")) if isinstance(data.get("object"), dict) else ""
        if not re.fullmatch(r"[A-Za-z0-9-]{16,64}", payment_id):
            raise PaymentError("Некорректное уведомление.")
        with self.db._conn() as conn:
            row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not row:
            return {"ok": True}
        payment = dict(row)
        # Не доверяем ни сумме, ни статусу из webhook: GET к API с секретом магазина.
        remote = self._request(payment["merchant"], "payments/" + payment_id)
        shop, _ = self.credentials(payment["merchant"])
        try:
            valid = (remote["id"] == payment_id and remote["recipient"]["account_id"] == shop
                and remote["amount"]["currency"] == payment["currency"]
                and Decimal(remote["amount"]["value"]) == Decimal(payment["amount"])
                and remote["metadata"]["order_id"] == payment["order_id"]
                and remote["metadata"]["kind"] == payment["kind"])
        except (KeyError, ValueError, TypeError, InvalidOperation):
            valid = False
        if not valid:
            raise PaymentError("Подтверждение платежа не соответствует заказу.")
        if remote.get("status") != "succeeded" or remote.get("paid") is not True:
            return {"ok": True}
        with self.db._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT status FROM payments WHERE id=?", (payment_id,)).fetchone()
            if current["status"] == "succeeded":
                return {"ok": True}
            conn.execute("UPDATE payments SET status='succeeded' WHERE id=?", (payment_id,))
            if payment["kind"] == "subscription":
                order = conn.execute("SELECT * FROM orders WHERE id=?", (payment["order_id"],)).fetchone()
                if not order or order["plan"] != "pro":
                    raise PaymentError("Тариф не найден.")
                conn.execute("UPDATE orders SET status='paid' WHERE id=?", (payment["order_id"],))
                now = datetime.now(timezone.utc)
                previous = conn.execute("SELECT v FROM settings WHERE k='subscription_until'").fetchone()
                if previous:
                    now = max(now, datetime.fromisoformat(previous["v"]))
                month = now.month - 1 + (12 if order["period"] == "yearly" else 1)
                year, month = now.year + month // 12, month % 12 + 1
                until = now.replace(year=year, month=month, day=min(now.day, calendar.monthrange(year, month)[1]))
                for key, value in (("subscription_plan", "pro"), ("subscription_until", until.isoformat())):
                    conn.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
            else:
                conn.execute("UPDATE purchases SET status='paid' WHERE id=?", (payment["order_id"],))
        return {"ok": True}
