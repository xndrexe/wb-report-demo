"""Рисует снимки листов для README из настоящего отчёта.

.venv/bin/python tools/render.py   # HTML → build/screens/, снимки — tools/shoot.py
Отчёт строится тем же кодом, что и в работе, на выдуманном магазине за 30 дней
до 14.10.2026; цвета ячеек берутся из Excel (шапка, итог, подсветка остатков).
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from datetime import date
from html import escape
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wbreport import __main__ as app  # noqa: E402
from wbreport import config, fake  # noqa: E402
from wbreport.api import WBClient  # noqa: E402

OUT = ROOT / "build" / "screens"
TODAY = date(2026, 10, 14)

CSS = """
* { box-sizing: border-box; margin: 0; }
body { background: #fff; }
.sheet { display: inline-block; padding: 0 0 4px; background: #fff; }
.sheet h1 { font: 600 14px Inter, sans-serif; padding: 10px 12px 0; color: #333; }
table { border-collapse: collapse; font: 12px/1.3 Inter, sans-serif; margin: 12px; }
td, th { border: 1px solid #d4d4d4; padding: 5px 8px; white-space: nowrap; }
th { color: #fff; font-weight: 600; text-align: left; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.total td { font-weight: 700; }
"""


ID_COLS = {"ID", "Артикул WB"}  # номера, а не суммы — без пробелов между разрядами


def fmt(v, is_id: bool = False) -> str:
    if v is None:
        return ""
    if is_id:
        return str(v)
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.1f}".replace(",", " ").replace(".", ",")
    if isinstance(v, int | float):
        return f"{int(v):,}".replace(",", " ")
    return str(v)


def render_sheet(path: Path, title: str, caption: str, rows: int = 100) -> str:
    ws = load_workbook(path)[title]
    data = list(ws.iter_rows())
    picked = [data[0], *data[1:-1][:rows], data[-1]] if len(data) - 2 > rows else data
    ids = {j for j, c in enumerate(data[0]) if c.value in ID_COLS}
    out = []
    for i, row in enumerate(picked):
        tag = "th" if i == 0 else "td"
        is_total = row[0].value == "Итого"
        cells = []
        for j, c in enumerate(row):
            color = c.fill.fgColor.rgb if c.fill.patternType == "solid" else None
            style = f' style="background:#{color[-6:]}"' if color else ""
            cls = ' class="num"' if tag == "td" and isinstance(c.value, int | float) else ""
            cells.append(f"<{tag}{cls}{style}>{escape(fmt(c.value, j in ids))}</{tag}>")
        out.append(f"<tr{' class=total' if is_total else ''}>" + "".join(cells) + "</tr>")
    return (
        f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
        f'<div class="sheet"><h1>{escape(caption)}</h1><table>{"".join(out)}</table></div>'
    )


def main() -> None:
    xlsx = Path(tempfile.mkdtemp()) / "wb_report.xlsx"
    with fake.FakeWB(fake.generate(TODAY)) as srv:
        cfg = replace(
            config.load(),
            token=fake.TOKEN,
            days=30,
            excel_path=xlsx,
            gsheet_id="",
            stats_url=srv.url,
            analytics_url=srv.url,
            advert_url=srv.url,
        )
        client = WBClient(
            cfg.token,
            stats_url=srv.url,
            analytics_url=srv.url,
            advert_url=srv.url,
            min_interval=0,
            sleep=lambda s: None,
        )
        app.run_once(cfg, client, today=TODAY)
    OUT.mkdir(parents=True, exist_ok=True)
    pages = {
        "1-prodazhi.html": (
            "Продажи и заказы",
            "Продажи и заказы по дням — 30 дней, на снимке последние 14 и итог",
            14,
        ),
        "2-ostatki.html": ("Остатки", "Остатки по складам: красным — товара меньше чем на 2 недели", 100),
        "3-reklama.html": ("Реклама", "Расходы на рекламу по кампаниям за 30 дней", 100),
    }
    for name, (title, caption, rows) in pages.items():
        (OUT / name).write_text(render_sheet(xlsx, title, caption, rows), "utf-8")
        print(OUT / name)


if __name__ == "__main__":
    main()
