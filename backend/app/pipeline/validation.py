from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

COLUMNS = ["order_id", "customer_id", "quantity", "unit_price", "order_date"]


@dataclass(frozen=True)
class ValidOrder:
    order_id: str
    customer_id: str
    quantity: int
    unit_price: Decimal
    order_date: date


def validate_order(data: dict) -> tuple[ValidOrder | None, str | None]:
    """Return the first error in schema order; never mutate the raw fields."""
    if set(data) != set(COLUMNS) or any(not isinstance(v, str) for v in data.values()):
        return None, "INVALID_COLUMNS"
    values = {key: value.strip() for key, value in data.items()}
    for field in ("order_id", "customer_id"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", values[field]):
            return None, f"INVALID_{field.upper()}"
    text = values["quantity"]
    if not re.fullmatch(r"[0-9]{1,7}", text) or not 1 <= int(text) <= 1_000_000:
        return None, "INVALID_QUANTITY"
    try:
        price = Decimal(values["unit_price"])
        if not price.is_finite() or not 0 <= price <= Decimal("99999999.99") or price.as_tuple().exponent < -2:
            return None, "INVALID_UNIT_PRICE"
    except InvalidOperation:
        return None, "INVALID_UNIT_PRICE"
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values["order_date"]):
            return None, "INVALID_ORDER_DATE"
        order_date = date.fromisoformat(values["order_date"])
    except ValueError:
        return None, "INVALID_ORDER_DATE"
    return ValidOrder(values["order_id"], values["customer_id"], int(text), price, order_date), None


def find_duplicate(order: ValidOrder, accepted_ids: dict[str, int]) -> int | None:
    """Only previously accepted IDs within this run count as duplicates."""
    return accepted_ids.get(order.order_id)
