import csv
import io
from dataclasses import dataclass

from app.pipeline.validation import COLUMNS


@dataclass(frozen=True)
class SourceRow:
    row_number: int
    original_record: str
    data: dict


@dataclass(frozen=True)
class ParsedSource:
    header: list[str]
    rows: list[SourceRow]


def parse_orders_csv(source: str) -> ParsedSource:
    """Parse without accepting a schema. Preserve faulty headers and every cell."""
    lines = list(io.StringIO(source, newline=""))
    reader = csv.reader(io.StringIO(source, newline=""), strict=True)
    try:
        header = next(reader)
        previous_line = reader.line_num
        rows = []
        for number, cells in enumerate(reader, start=1):
            text = "".join(lines[previous_line:reader.line_num])
            previous_line = reader.line_num
            if len(set(header)) != len(header):
                data = {"_columns": header, "_cells": cells}
            elif len(cells) != len(header):
                data = {"_cells": cells}
            else:
                data = dict(zip(header, cells))
            rows.append(SourceRow(number, text, data))
        return ParsedSource(header, rows)
    except (csv.Error, StopIteration) as exc:
        raise ValueError("Empty or malformed CSV") from exc


def read_orders_csv(source: str) -> list[SourceRow]:
    """Keep the original strict five-column API for legacy callers."""
    parsed = parse_orders_csv(source)
    if parsed.header != COLUMNS:
        raise ValueError(f"CSV header must be {','.join(COLUMNS)}")
    return parsed.rows
