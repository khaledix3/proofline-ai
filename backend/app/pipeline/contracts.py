from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from app.pipeline.timestamps import parse_event_timestamp
from app.pipeline.validation import COLUMNS, ValidOrder, validate_order

ColumnPolicy = Literal["reject", "allow"]


@dataclass(frozen=True)
class Field:
    name: str
    data_type: str
    required: bool = True


@dataclass(frozen=True)
class SchemaContract:
    version: str
    fields: tuple[Field, ...]
    monitor_schema: bool

    def definition(self) -> dict:
        # A fresh snapshot; callers cannot modify the frozen registry.
        return {"version": self.version, "columns": [
            {"name": field.name, "type": field.data_type, "required": field.required}
            for field in self.fields]}


BASE_FIELDS = (Field("order_id", "string"), Field("customer_id", "string"),
               Field("quantity", "integer"), Field("unit_price", "decimal"), Field("order_date", "date"))
CONTRACTS = MappingProxyType({
    "orders.v1": SchemaContract("orders.v1", BASE_FIELDS, False),
    "orders.v2": SchemaContract("orders.v2", BASE_FIELDS + (Field("event_timestamp", "timestamp_with_timezone"),), True),
})


def get_contract(version: str) -> SchemaContract:
    try:
        return CONTRACTS[version]
    except KeyError:
        raise ValueError("Unknown expected schema version") from None


def validate_contract_order(data: dict, version: str = "orders.v1", policy: ColumnPolicy = "reject") -> tuple[ValidOrder | None, str | None]:
    contract = get_contract(version)
    if version == "orders.v1":
        return validate_order(data)  # Preserve the original five-column behavior.
    columns = {field.name for field in contract.fields}
    if (not columns <= set(data) or (policy == "reject" and set(data) != columns)
            or any(not isinstance(value, str) for value in data.values())):
        return None, "INVALID_COLUMNS"
    order, reason = validate_order({name: data[name] for name in COLUMNS})
    if reason:
        return None, reason
    try:
        parse_event_timestamp(data["event_timestamp"])
    except ValueError:
        return None, "INVALID_EVENT_TIMESTAMP"
    return order, None
