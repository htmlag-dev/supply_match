"""Заказы товаров. Оплата продавцу и подписка приложения разделены."""
import re
import uuid
from datetime import datetime, timezone
from app.services.cost_service import CURRENCIES, number, rounded
from app.services.payment_service import YooKassaGateway


class PurchaseService:
    def __init__(self, db):
        self.db = db
        self.payments = YooKassaGateway(db)

    def create(self, data):
        sid = data.get("supplier_id")
        if isinstance(sid, bool) or not isinstance(sid, int):
            raise ValueError("Выберите продавца из вашей базы.")
        seller = self.db.get_supplier(sid)
        if not seller:
            raise ValueError("Продавец не найден. Сначала добавьте его в базу.")
        product = data.get("product", "")
        email, address = data.get("buyer_email", ""), data.get("address", "")
        if not isinstance(product, str) or not product.strip() or len(product) > 500:
            raise ValueError("Укажите товар для заказа.")
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("Укажите корректную почту покупателя.")
        if not isinstance(address, str) or not address.strip() or len(address) > 2000:
            raise ValueError("Укажите адрес доставки или условия самовывоза.")
        currency = data.get("currency")
        trade_format = data.get("trade_format")
        token = data.get("idempotency_key", "")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", token):
            raise ValueError("Некорректный идентификатор заказа.")
        if currency not in CURRENCIES or trade_format not in ("B2B", "B2C", "C2C"):
            raise ValueError("Выберите валюту и формат сделки.")
        if data.get("consent") is not True:
            raise ValueError("Подтвердите, что согласовали товар, цену и доставку с продавцом.")
        quantity = number(data.get("quantity"), "Количество", True)
        price = number(rounded(number(data.get("unit_price"), "Цена единицы", True)), "Цена единицы", True)
        shipping = number(rounded(number(data.get("shipping", 0), "Доставка")), "Доставка")
        total = quantity * price + shipping
        if total > 1000000000000:
            raise ValueError("Слишком большая сумма заказа.")
        order = self.db.create_purchase({"id": str(uuid.uuid4()), "supplier_id": sid,
            "seller_name": seller["name"], "product": product.strip(), "quantity": str(quantity),
            "unit_price": rounded(price), "shipping": rounded(shipping), "amount": rounded(total),
            "currency": currency, "buyer_email": email.strip(), "address": address.strip(),
            "trade_format": trade_format, "status": "awaiting_integration",
            "created_at": datetime.now(timezone.utc).isoformat(), "idempotency_key": token})
        checkout = self.payments.checkout("purchase", order, "seller_" + str(sid))
        if checkout:
            return {"order": order, **checkout}
        return {"order": order, "payment_enabled": False, "checkout_url": None,
            "message": "Заказ сохранён. Оплата этому продавцу пока не подключена. Деньги не списаны; заказ и контактные данные продавцу автоматически не отправляются."}
