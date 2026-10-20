"""Рисует снимки листов для README из настоящего отчёта.

.venv/bin/python tools/render.py   # HTML → build/screens/, снимки — tools/shoot.py
Отчёты WB и Ozon строятся тем же кодом, что и в работе (как --demo), на
выдуманном магазине за 30 дней до 14.10.2026; цвета ячеек берутся из Excel
(шапка, итог, подсветка остатков).
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
from wbreport import config, fake, ozon_fake  # noqa: E402
from wbreport.api import WBClient  # noqa: E402
from wbreport.ozon_api import OzonAdsClient, OzonClient  # noqa: E402

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


ID_COLS = {"ID", "Артикул WB", "SKU Ozon"}  # номера, а не суммы — без пробелов между разрядами


def fmt(v, is_id: bool = False, decimal: bool = False) -> str:
    """decimal — в столбце есть дроби: все числа с одним знаком, чтобы «2» не стояло рядом с «2,2»."""
    if v is None:
        return ""
    if is_id:
        return str(v)
    if isinstance(v, int | float):
        text = f"{abs(v):,.1f}" if decimal else f"{round(abs(v)):,}"
        sign = "−" if round(v, 1 if decimal else 0) < 0 else ""  # настоящий минус: дефис отрывается от цифр
        return sign + text.replace(",", " ").replace(".", ",")
    return str(v)


def render_sheet(path: Path, title: str, caption: str, rows: int = 100) -> str:
    ws = load_workbook(path)[title]
    data = list(ws.iter_rows())
    picked = [data[0], *data[1:-1][:rows], data[-1]] if len(data) - 2 > rows else data
    ids = {j for j, c in enumerate(data[0]) if c.value in ID_COLS}
    decimal = {
        j
        for row in data[1:]
        for j, c in enumerate(row)
        if isinstance(c.value, float) and not c.value.is_integer()
    }
    out = []
    for i, row in enumerate(picked):
        tag = "th" if i == 0 else "td"
        is_total = row[0].value == "Итого"
        cells = []
        for j, c in enumerate(row):
            color = c.fill.fgColor.rgb if c.fill.patternType == "solid" else None
            style = f' style="background:#{color[-6:]}"' if color else ""
            cls = ' class="num"' if tag == "td" and isinstance(c.value, int | float) else ""
            cells.append(f"<{tag}{cls}{style}>{escape(fmt(c.value, j in ids, j in decimal))}</{tag}>")
        out.append(f"<tr{' class=total' if is_total else ''}>" + "".join(cells) + "</tr>")
    return (
        f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
        f'<div class="sheet"><h1>{escape(caption)}</h1><table>{"".join(out)}</table></div>'
    )


def wb_xlsx() -> Path:
    xlsx = Path(tempfile.mkdtemp()) / "wb_report.xlsx"
    with fake.FakeWB(fake.generate(TODAY)) as srv:
        # адреса и токен run_once берёт из клиента, в настройках нужны только период и куда сохранить
        cfg = replace(config.load(), days=30, excel_path=xlsx, gsheet_id="")
        client = WBClient(
            fake.TOKEN,
            stats_url=srv.url,
            analytics_url=srv.url,
            advert_url=srv.url,
            min_interval=0,
            sleep=lambda s: None,
        )
        app.run_once(cfg, client, today=TODAY)
    return xlsx


def ozon_xlsx() -> Path:
    """Ozon с рекламой — те же выдуманные магазин и кампании, что в --market ozon --demo."""
    xlsx = Path(tempfile.mkdtemp()) / "ozon_report.xlsx"
    with (
        ozon_fake.FakeOzon(ozon_fake.generate(TODAY)) as srv,
        ozon_fake.FakePerformance(ozon_fake.generate_ads(TODAY)) as perf,
    ):
        cfg = replace(config.load(), market="ozon", days=30, excel_path=xlsx, gsheet_id="")
        client = OzonClient(ozon_fake.CLIENT_ID, ozon_fake.API_KEY, url=srv.url)
        ads = OzonAdsClient(ozon_fake.ADS_CLIENT_ID, ozon_fake.ADS_SECRET, url=perf.url)
        app.run_ozon(cfg, client, today=TODAY, ads=ads)
    return xlsx


def main() -> None:
    wb, ozon = wb_xlsx(), ozon_xlsx()
    OUT.mkdir(parents=True, exist_ok=True)
    pages = {
        "1-prodazhi.html": (
            wb,
            "Продажи и заказы",
            "Продажи и заказы по дням — 30 дней, на снимке последние 14 и итог",
            14,
        ),
        "2-ostatki.html": (wb, "Остатки", "Остатки по складам: красным — товара меньше чем на 2 недели", 100),
        "3-reklama.html": (wb, "Реклама", "Расходы на рекламу по кампаниям за 30 дней", 100),
        "4-ozon-prodazhi.html": (
            ozon,
            "Продажи и заказы",
            "Ozon: продажи и заказы по дням с рекламой и ДРР — 30 дней, на снимке последние 14 и итог",
            14,
        ),
        "5-ozon-reklama.html": (
            ozon,
            "Реклама",
            "Ozon: реклама по кампаниям за 30 дней — расход, клики, ДРР",
            100,
        ),
    }
    for name, (xlsx, title, caption, rows) in pages.items():
        (OUT / name).write_text(render_sheet(xlsx, title, caption, rows), "utf-8")
        print(OUT / name)


if __name__ == "__main__":
    main()
