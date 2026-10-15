"""Сводка из сырых ответов WB: три листа — продажи и заказы, остатки, реклама. Без сети."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

AD_TYPES = {
    4: "В каталоге",
    5: "В карточке товара",
    6: "В поиске",
    7: "В рекомендациях",
    8: "Автоматическая",
    9: "Аукцион",
}
LOW_STOCK_DAYS = 14  # остатка меньше чем на две недели — пора везти поставку
TRANSIT_PREFIXES = ("В пути", "Всего")  # служебные строки отчёта об остатках, а не склады


@dataclass
class Sheet:
    title: str
    headers: list[str]
    rows: list[list] = field(default_factory=list)
    money_cols: set[int] = field(default_factory=set)  # номера столбцов с рублями, с 0
    total: list | None = None  # строка «Итого» внизу
    warn: tuple[int, int] | None = None  # (столбец, порог): меньше порога — подсветить красным


def _day(ts: str) -> str:
    return ts[:10]


def _pct(part: float, whole: float) -> float | str:
    return round(100 * part / whole, 1) if whole else ""


def sales_sheet(orders: list[dict], sales: list[dict], ads: list[dict], start: date, end: date) -> Sheet:
    """По дням: заказы, отмены, выкупы, возвраты, деньги и доля рекламы в заказах."""
    d = defaultdict(lambda: defaultdict(float))
    for o in orders:
        x = d[_day(o["date"])]
        x["orders"] += 1
        x["orders_rub"] += abs(o.get("priceWithDisc", 0))
        if o.get("isCancel"):
            x["cancels"] += 1
    for s in sales:
        x = d[_day(s["date"])]
        # S — продажа, R — возврат; суммы берём по модулю и вычитаем сами,
        # чтобы не зависеть от знака, с которым WB отдаёт возвраты
        sign = -1 if str(s.get("saleID", "")).startswith("R") else 1
        x["buyouts" if sign > 0 else "returns"] += 1
        x["sales_rub"] += sign * abs(s.get("priceWithDisc", 0))
        x["for_pay"] += sign * abs(s.get("forPay", 0))
    for a in ads:
        d[_day(a["updTime"])]["ads"] += abs(a.get("updSum", 0))

    sheet = Sheet(
        "Продажи и заказы",
        [
            "Дата",
            "Заказы, шт",
            "Заказы, ₽",
            "Отмены, шт",
            "Выкупы, шт",
            "Возвраты, шт",
            "Продажи, ₽",
            "К перечислению, ₽",
            "Реклама, ₽",
            "ДРР, %",
        ],
        money_cols={2, 6, 7, 8},
    )
    tot = defaultdict(float)
    day = end
    while day >= start:  # свежие дни сверху
        x = d.get(day.isoformat(), {})
        vals = [
            x.get(k, 0)
            for k in ("orders", "orders_rub", "cancels", "buyouts", "returns", "sales_rub", "for_pay", "ads")
        ]
        for k, v in zip(
            ("orders", "orders_rub", "cancels", "buyouts", "returns", "sales_rub", "for_pay", "ads"),
            vals,
            strict=True,
        ):
            tot[k] += v
        sheet.rows.append([day.strftime("%d.%m.%Y"), *(_num(v) for v in vals), _pct(vals[7], vals[1])])
        day -= timedelta(days=1)
    sheet.total = [
        "Итого",
        *(
            _num(tot[k])
            for k in ("orders", "orders_rub", "cancels", "buyouts", "returns", "sales_rub", "for_pay", "ads")
        ),
        _pct(tot["ads"], tot["orders_rub"]),
    ]
    return sheet


def _num(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(v, 2)


def stocks_sheet(stocks: list[dict], orders: list[dict], days: int) -> Sheet:
    """Артикул × склад, итог, в пути, средние заказы в день и на сколько дней хватит."""
    ordered = defaultdict(int)
    for o in orders:
        if not o.get("isCancel"):
            ordered[o.get("nmId")] += 1
    warehouses = sorted(
        {
            w["warehouseName"]
            for s in stocks
            for w in s.get("warehouses", [])
            if not w["warehouseName"].startswith(TRANSIT_PREFIXES)
        }
    )
    sheet = Sheet(
        "Остатки",
        [
            "Артикул продавца",
            "Артикул WB",
            "Предмет",
            "Бренд",
            *warehouses,
            "Всего на складах",
            "Едет к покупателям",
            "Заказов в день",
            "Хватит на, дней",
        ],
    )
    sheet.warn = (len(sheet.headers) - 1, LOW_STOCK_DAYS)
    for s in sorted(stocks, key=lambda s: s.get("vendorCode", "")):
        qty = {w["warehouseName"]: w.get("quantity", 0) for w in s.get("warehouses", [])}
        total = sum(v for k, v in qty.items() if not k.startswith(TRANSIT_PREFIXES))
        rate = ordered.get(s.get("nmId"), 0) / days
        sheet.rows.append(
            [
                s.get("vendorCode", ""),
                s.get("nmId", ""),
                s.get("subjectName", ""),
                s.get("brand", ""),
                *(qty.get(w, 0) for w in warehouses),
                total,
                qty.get("В пути до получателей", 0),
                round(rate, 1),
                int(total / rate) if rate else "",
            ]
        )
    return sheet


def _stamp(ts: str) -> str:
    """2026-10-14T09:48:00 → 14.10.2026 09:48."""
    return f"{ts[8:10]}.{ts[5:7]}.{ts[:4]} {ts[11:16]}" if ts else ""


def ads_sheet(ads: list[dict]) -> Sheet:
    """Расходы по кампаниям за период."""
    camp: dict[int, dict] = {}
    for a in ads:
        c = camp.setdefault(
            a["advertId"],
            {"name": a.get("campName", ""), "type": a.get("advertType"), "sum": 0.0, "n": 0, "last": ""},
        )
        c["sum"] += abs(a.get("updSum", 0))
        c["n"] += 1
        c["last"] = max(c["last"], a.get("updTime", ""))
    sheet = Sheet(
        "Реклама", ["Кампания", "ID", "Тип", "Расход, ₽", "Списаний", "Последнее списание"], money_cols={3}
    )
    for cid, c in sorted(camp.items(), key=lambda kv: -kv[1]["sum"]):
        sheet.rows.append(
            [
                c["name"],
                cid,
                AD_TYPES.get(c["type"], "" if c["type"] is None else str(c["type"])),  # без «None» в ячейке
                _num(c["sum"]),
                c["n"],
                _stamp(c["last"]),
            ]
        )
    sheet.total = [
        "Итого",
        "",
        "",
        _num(sum(c["sum"] for c in camp.values())),
        sum(c["n"] for c in camp.values()),
        "",
    ]
    return sheet


def build(orders, sales, stocks, ads, start: date, end: date) -> list[Sheet]:
    days = (end - start).days + 1
    return [sales_sheet(orders, sales, ads, start, end), stocks_sheet(stocks, orders, days), ads_sheet(ads)]
