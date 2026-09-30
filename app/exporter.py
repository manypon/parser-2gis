from __future__ import annotations

import csv
import json
import datetime as dt
from pathlib import Path

from openpyxl import Workbook

from .models import Organization
from . import config

HEADS = ["Название", "Рубрика", "Адрес", "Телефон", "Оценка", "Ссылки"]
KEYS = ["name", "category", "address", "phone", "rating", "links"]


def _base_name(query: str, city: str) -> str:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw = f"yandex_maps_{query}_{city}_{ts}"
    safe = "".join(c for c in raw if c.isalnum() or c in "_-").strip("_")
    return safe or f"yandex_maps_export_{ts}"


def export(orgs: list[Organization], fmt: str, query: str, city: str, output_dir: str | None = None) -> str:
    rows = [o.to_row() for o in orgs]
    out_dir = Path(output_dir) if output_dir else config.EXPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _base_name(query, city)

    if fmt == "json":
        path = out_dir / f"{base}.json"
        path.write_text(
            json.dumps([o.to_dict() for o in orgs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)

    if fmt == "csv":
        path = out_dir / f"{base}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(HEADS)
            for row in rows:
                writer.writerow([row[k] for k in KEYS])
        return str(path)

    if fmt == "xlsx":
        path = out_dir / f"{base}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "YandexMaps"
        ws.append(HEADS)
        for row in rows:
            ws.append([row[k] for k in KEYS])
        for col_cells in ws.columns:
            length = max(len(str(c.value or "")) for c in col_cells)
            ws.column_dimensions[col_cells[0].column_letter].width = min(60, length + 2)
        wb.save(path)
        return str(path)

    if fmt == "html":
        path = out_dir / f"{base}.html"
        th = "".join(f"<th>{h}</th>" for h in HEADS)
        body_rows = "".join(
            "<tr>" + "".join(f"<td>{_esc(row[k])}</td>" for k in KEYS) + "</tr>" for row in rows
        )
        html = (
            "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>"
            f"<title>{_esc(query)} {_esc(city)}</title></head><body>"
            "<table border='1' cellpadding='6' style='border-collapse:collapse;font-family:sans-serif'>"
            f"<thead><tr>{th}</tr></thead><tbody>{body_rows}</tbody></table></body></html>"
        )
        path.write_text(html, encoding="utf-8")
        return str(path)

    raise ValueError(f"Неизвестный формат экспорта: {fmt}")


def _esc(v) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
