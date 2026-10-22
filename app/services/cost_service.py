"""Оценка стоимости по введённым пользователем суммам, без котировок и ставок налогов."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCIES = {"RUB", "USD", "EUR", "CNY", "KZT", "TRY", "AED"}


def number(value, label, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Укажите число: " + label) from None
    if not result.is_finite() or result < 0 or result > Decimal("1000000000000") or (positive and result <= 0):
        raise ValueError("Недопустимое значение: " + label)
    if result.as_tuple().exponent < -8:
        raise ValueError("Слишком много знаков после запятой: " + label)
    return result


def rounded(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def calculate_cost(data):
    currency, base = data.get("currency"), data.get("base_currency")
    if currency not in CURRENCIES or base not in CURRENCIES:
        raise ValueError("Выберите поддерживаемую валюту.")
    quantity = number(data.get("quantity"), "Количество", True)
    price = number(data.get("unit_price"), "Цена")
    rate = Decimal(1) if currency == base else number(data.get("rate"), "Курс", True)
    shipping = number(data.get("shipping", 0), "Доставка")
    taxes = number(data.get("taxes", 0), "Пошлины и налоги")
    other = number(data.get("other", 0), "Прочие расходы")
    goods = quantity * price * rate
    total = goods + shipping + taxes + other
    if total > Decimal("1000000000000000"):
        raise ValueError("Сумма превышает допустимый предел расчёта.")
    result = {"currency": base, "goods": rounded(goods), "shipping": rounded(shipping),
              "taxes": rounded(taxes), "other": rounded(other), "total": rounded(total),
              "per_unit": rounded(total / quantity), "rate": str(rate)}
    if data.get("resale_price") not in (None, ""):
        revenue = number(data["resale_price"], "Цена перепродажи") * quantity
        profit = revenue - total
        result.update(revenue=rounded(revenue), profit=rounded(profit), margin=rounded(profit / revenue * 100) if revenue else None)
    return result
