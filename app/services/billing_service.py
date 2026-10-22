"""Каталог тарифов и заявки. Подключение эквайринга — через PaymentGateway."""
import re
import uuid
from datetime import datetime, timezone
from app.services.access_service import AccessService
from app.services.payment_service import YooKassaGateway

PLANS = [
    {"id": "start", "name": "Старт", "monthly": 0, "yearly": 0,
     "description": "Для редких покупок", "features": ["До 20 продавцов в базе", "5 AI-действий в месяц с вашим ключом", "Заказы, калькулятор и экспорт CSV"]},
    {"id": "pro", "name": "Профи", "monthly": 990, "yearly": 9900,
     "description": "Для регулярных закупок и перепродажи", "features": ["До 1 000 продавцов в базе", "300 AI-действий в месяц с вашим ключом", "История поиска и сравнений", "Все форматы B2B, B2C и C2C"]},
    {"id": "team", "name": "Команда", "monthly": 2490, "yearly": 24900,
     "description": "Для отдела закупок", "features": ["Всё из тарифа Профи", "Планируется: общая база команды", "Планируется: роли и несколько проектов"]},
]


class PaymentGateway:
    """Замените адаптер на создание платежа в выбранном сервисе.

    Верните HTTPS checkout_url, сохраните ID платежа и активируйте тариф
    ТОЛЬКО после проверки подписи webhook, суммы и валюты.
    Клиентский редирект не является подтверждением платежа.
    """
    def create_checkout(self, order):
        return {"checkout_url": None, "payment_enabled": False,
                "message": "Заявка сохранена. Приём платежей пока не подключён. Деньги не списаны, подписка не оформлена."}


class BillingService:
    def __init__(self, db, gateway=None):
        self.db, self.gateway = db, gateway or PaymentGateway()
        self.payments = YooKassaGateway(db)

    def state(self):
        return {"plans": PLANS, "current_plan": AccessService(self.db).state()["plan"], "payment_enabled": self.payments.enabled("billing"),
                "orders": self.db.list_orders()}

    def checkout(self, data):
        plan = next((p for p in PLANS if p["id"] == data.get("plan") and p["monthly"]), None)
        period = data.get("period")
        if not plan or period not in ("monthly", "yearly"):
            raise ValueError("Выберите платный тариф и период оплаты.")
        email = str(data.get("email", "")).strip()
        company = str(data.get("company", "")).strip()
        token = str(data.get("idempotency_key", ""))
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise ValueError("Укажите корректную электронную почту.")
        if len(company) > 200:
            raise ValueError("Название компании слишком длинное.")
        if data.get("consent") is not True:
            raise ValueError("Подтвердите согласие на сохранение заявки.")
        if not re.fullmatch(r"[A-Za-z0-9-]{16,80}", token):
            raise ValueError("Неверный идентификатор заявки. Обновите страницу.")
        order = self.db.create_order({"id": str(uuid.uuid4()), "plan": plan["id"], "period": period,
            "amount": plan[period] * 100, "currency": "RUB", "email": email, "company": company,
            "status": "awaiting_integration", "created_at": datetime.now(timezone.utc).isoformat(),
            "idempotency_key": token})
        checkout = None if data.get("native_request") is True else self.payments.checkout("subscription", order, "billing")
        return {"order": order, **(checkout or self.gateway.create_checkout(order))}
