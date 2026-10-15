"""Сводка из сырых ответов Ozon: продажи и заказы по дням, остатки, реклама, заказы и возвраты по товарам.

Без сети. Даты Ozon приходят в UTC — день считаем по Москве, как в кабинете продавца.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .report import LOW_STOCK_DAYS, Sheet, _num, _pct

MSK = ZoneInfo("Europe/Moscow")
RETURN_TYPES = {"FullReturn", "PartialReturn", "ClientReturn"}  # Cancellation — отмена до доставки
OWN_STOCK_TYPES = {"fbs", "rfbs"}  # остатки на своём складе; fbo считаем по аналитике Ozon
AD_TYPES = {  # advObjectType кампании Performance API
    "SKU": "Оплата за клик",
    "SEARCH_PROMO": "Оплата за заказ",
    "BANNER": "Баннер",
    "VIDEO_BANNER": "Видеобаннер",
}
AD_STATES = {
    "CAMPAIGN_STATE_RUNNING": "Идёт",
    "CAMPAIGN_STATE_PLANNED": "Запланирована",
    "CAMPAIGN_STATE_STOPPED": "Кончился бюджет",
    "CAMPAIGN_STATE_INACTIVE": "Выключена",
    "CAMPAIGN_STATE_ARCHIVED": "В архиве",
    "CAMPAIGN_STATE_FINISHED": "Завершена",
    "CAMPAIGN_STATE_MODERATION_DRAFT": "Черновик",
    "CAMPAIGN_STATE_MODERATION_IN_PROGRESS": "На модерации",
    "CAMPAIGN_STATE_MODERATION_FAILED": "Не прошла модерацию",
}


def msk_day(ts: str) -> str:
    """2026-10-13T22:30:00Z → 2026-10-14: после 21:00 UTC в Москве уже следующий день."""
    if not ts:
        return ""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(MSK).date().isoformat()


def _price(item: dict) -> float:
    """Цена товара в отправлении: у FBO — строкой, у FBS (v4) — объектом с amount."""
    v = item.get("price")
    if isinstance(v, dict):
        v = v.get("amount")
    return float(v or 0)


def _money(obj: dict | None) -> float:
    return float((obj or {}).get("amount") or 0)


def _rub(v) -> float:
    """Сумма Performance API: строка с запятой и, бывает, пробелами («1 234,56») или число."""
    if isinstance(v, int | float):
        return float(v)
    s = str(v or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    return float(s) if s else 0.0


def _is_return(r: dict) -> bool:
    return r.get("type") in RETURN_TYPES


def sales_sheet(
    postings: list[dict],
    returns: list[dict],
    accruals: list[dict],
    start: date,
    end: date,
    ads: list[dict] | None = None,
) -> Sheet:
    """По дням: заказы FBO и FBS, отмены, возвраты, начисления Ozon; с ключом рекламы — расход и ДРР.

    ads=None — ключа Performance API нет: столбцов рекламы нет вовсе, чтобы не показывать ложный ноль.
    ДРР — расход на рекламу к заказам дня в рублях, включая отменённые, как в части WB.
    """
    keys = ("units", "fbo", "fbs", "rub", "cancels", "returns", "posting_acc", "other_acc", "total_acc")
    if ads is not None:
        keys += ("ads",)
    d = defaultdict(lambda: defaultdict(float))
    for p in postings:
        x = d[msk_day(p.get("in_process_at", ""))]
        for item in p.get("products", []):
            qty = item.get("quantity", 1)
            x["units"] += qty
            x["fbo" if p.get("delivery_schema") == "FBO" else "fbs"] += qty
            x["rub"] += _price(item) * qty
            if p.get("status") == "cancelled":
                x["cancels"] += qty
    for r in returns:
        if _is_return(r):
            d[msk_day(r.get("logistic", {}).get("return_date", ""))]["returns"] += r.get("product", {}).get(
                "quantity", 1
            )
    for a in accruals:
        x = d[str(a.get("date", ""))[:10]]
        amount = _money(a.get("total_amount"))
        x["posting_acc" if a.get("accrued_category") == "POSTING" else "other_acc"] += amount
        x["total_acc"] += amount
    for a in ads or []:
        d[str(a.get("date", ""))[:10]]["ads"] += _rub(a.get("moneySpent"))

    sheet = Sheet(
        "Продажи и заказы",
        [
            "Дата",
            "Заказы, шт",
            "FBO, шт",
            "FBS, шт",
            "Заказы, ₽",
            "Отмены, шт",
            "Возвраты, шт",
            "Начислено по заказам, ₽",
            "Услуги и прочее, ₽",
            "Итого начислено, ₽",
        ],
        money_cols={4, 7, 8, 9},
    )
    if ads is not None:
        sheet.headers += ["Реклама, ₽", "ДРР, %"]
        sheet.money_cols.add(10)
    tot = defaultdict(float)
    day = end
    while day >= start:  # свежие дни сверху
        x = d.get(day.isoformat(), {})
        vals = [x.get(k, 0) for k in keys]
        for k, v in zip(keys, vals, strict=True):
            tot[k] += v
        row = [day.strftime("%d.%m.%Y"), *(_num(v) for v in vals)]
        if ads is not None:
            row.append(_pct(x.get("ads", 0), x.get("rub", 0)))
        sheet.rows.append(row)
        day -= timedelta(days=1)
    sheet.total = ["Итого", *(_num(tot[k]) for k in keys)]
    if ads is not None:
        sheet.total.append(_pct(tot["ads"], tot["rub"]))
    return sheet


def _ordered_units(postings: list[dict]) -> Counter:
    """Сколько штук каждого артикула заказали и не отменили."""
    c: Counter = Counter()
    for p in postings:
        if p.get("status") == "cancelled":
            continue
        for item in p.get("products", []):
            c[item.get("offer_id", "")] += item.get("quantity", 1)
    return c


def stocks_sheet(products: list[dict], fbo: list[dict], postings: list[dict], days: int) -> Sheet:
    """Артикул × кластер Ozon, свой склад, в пути, заказы в день и на сколько дней хватит."""
    ordered = _ordered_units(postings)
    names = {i.get("offer_id"): i.get("name", "") for p in postings for i in p.get("products", [])}
    by_sku: dict[int, dict] = defaultdict(lambda: {"clusters": Counter(), "transit": 0, "back": 0})
    for row in fbo:
        s = by_sku[row.get("sku")]
        s["clusters"][row.get("cluster_name") or row.get("warehouse_name", "")] += row.get(
            "available_stock_count", 0
        )
        s["transit"] += row.get("transit_stock_count", 0)
        s["back"] += row.get("return_from_customer_stock_count", 0)
        if row.get("name"):
            names[row.get("offer_id")] = row["name"]
    clusters = sorted({c for s in by_sku.values() for c in s["clusters"]})
    sheet = Sheet(
        "Остатки",
        [
            "Артикул продавца",
            "SKU Ozon",
            "Товар",
            *clusters,
            "На складах Ozon",
            "Свой склад",
            "Едет на склады Ozon",
            "Возвращается от покупателей",
            "Заказов в день",
            "Хватит на, дней",
        ],
    )
    sheet.warn = (len(sheet.headers) - 1, LOW_STOCK_DAYS)
    for p in sorted(products, key=lambda p: p.get("offer_id", "")):
        stocks = p.get("stocks", [])
        sku = next((s.get("sku") for s in stocks if s.get("sku")), "")
        own = sum(
            max(0, s.get("present", 0) - s.get("reserved", 0))
            for s in stocks
            if s.get("type") in OWN_STOCK_TYPES
        )
        f = by_sku.get(sku, {"clusters": Counter(), "transit": 0, "back": 0})
        at_ozon = sum(f["clusters"].values())
        rate = ordered.get(p.get("offer_id", ""), 0) / days
        sheet.rows.append(
            [
                p.get("offer_id", ""),
                sku,
                names.get(p.get("offer_id"), ""),
                *(f["clusters"].get(c, 0) for c in clusters),
                at_ozon,
                own,
                f["transit"],
                f["back"],
                round(rate, 1),
                int((at_ozon + own) / rate) if rate else "",
            ]
        )
    return sheet


def ads_sheet(campaigns: list[dict], daily: list[dict]) -> Sheet:
    """По кампаниям за период: расход, показы, клики, заказы с рекламы и ДРР к ним.

    Кампании без показов и расхода в периоде не выводим: архив за годы только мешает читать.
    """
    info = {str(c.get("id")): c for c in campaigns}
    camp: dict[str, dict] = {}
    for r in daily:
        cid = str(r.get("id", ""))
        c = camp.setdefault(
            cid,
            {
                "title": r.get("title", ""),
                "spent": 0.0,
                "views": 0,
                "clicks": 0,
                "orders": 0,
                "orders_rub": 0.0,
                "days": 0,
                "last": "",
            },
        )
        spent = _rub(r.get("moneySpent"))
        c["spent"] += spent
        c["views"] += int(_rub(r.get("views")))
        c["clicks"] += int(_rub(r.get("clicks")))
        c["orders"] += int(_rub(r.get("orders")))
        c["orders_rub"] += _rub(r.get("ordersMoney"))
        if spent > 0:
            c["days"] += 1
            c["last"] = max(c["last"], str(r.get("date", ""))[:10])
    sheet = Sheet(
        "Реклама",
        [
            "Кампания",
            "ID",
            "Тип",
            "Состояние",
            "Расход, ₽",
            "Показы",
            "Клики",
            "CTR, %",
            "Заказы с рекламы, шт",
            "Заказы с рекламы, ₽",
            "ДРР, %",
            "Дней с расходом",
            "Последний расход",
        ],
        money_cols={4, 9},
    )
    active = {k: c for k, c in camp.items() if c["spent"] or c["views"]}
    for cid, c in sorted(active.items(), key=lambda kv: -kv[1]["spent"]):
        meta = info.get(cid, {})
        kind, state = meta.get("advObjectType", ""), meta.get("state", "")
        sheet.rows.append(
            [
                meta.get("title") or c["title"],
                int(cid) if cid.isdigit() else cid,  # Ozon отдаёт id строкой — в Excel нужно число
                AD_TYPES.get(kind, kind),
                AD_STATES.get(state, state),
                _num(c["spent"]),
                c["views"],
                c["clicks"],
                _pct(c["clicks"], c["views"]),
                c["orders"],
                _num(c["orders_rub"]),
                _pct(c["spent"], c["orders_rub"]),
                c["days"],
                date.fromisoformat(c["last"]).strftime("%d.%m.%Y") if c["last"] else "",
            ]
        )
    t = {k: sum(c[k] for c in active.values()) for k in ("spent", "views", "clicks", "orders", "orders_rub")}
    sheet.total = [
        "Итого",
        "",
        "",
        "",
        _num(t["spent"]),
        t["views"],
        t["clicks"],
        _pct(t["clicks"], t["views"]),
        t["orders"],
        _num(t["orders_rub"]),
        _pct(t["spent"], t["orders_rub"]),
        "",
        "",
    ]
    return sheet


def items_sheet(postings: list[dict], returns: list[dict]) -> Sheet:
    """По артикулам: заказано, отменено, возвращено и самая частая причина возврата."""
    a: dict[str, dict] = {}

    def row(offer: str, sku, name: str) -> dict:
        return a.setdefault(
            offer,
            {"sku": sku, "name": name, "units": 0, "rub": 0.0, "cancels": 0, "returns": 0, "why": Counter()},
        )

    for p in postings:
        for item in p.get("products", []):
            x = row(item.get("offer_id", ""), item.get("sku", ""), item.get("name", ""))
            qty = item.get("quantity", 1)
            x["units"] += qty
            x["rub"] += _price(item) * qty
            if p.get("status") == "cancelled":
                x["cancels"] += qty
    for r in returns:
        if not _is_return(r):
            continue
        prod = r.get("product", {})
        x = row(prod.get("offer_id", ""), prod.get("sku", ""), prod.get("name", ""))
        x["returns"] += prod.get("quantity", 1)
        if r.get("return_reason_name"):
            x["why"][r["return_reason_name"]] += 1

    sheet = Sheet(
        "Заказы и возвраты по товарам",
        [
            "Артикул продавца",
            "SKU Ozon",
            "Товар",
            "Заказано, шт",
            "Заказано, ₽",
            "Отменено, шт",
            "Возвратов, шт",
            "Возвраты, %",
            "Частая причина возврата",
        ],
        money_cols={4},
    )
    for offer, x in sorted(a.items(), key=lambda kv: -kv[1]["rub"]):
        sheet.rows.append(
            [
                offer,
                x["sku"],
                x["name"],
                x["units"],
                _num(x["rub"]),
                x["cancels"],
                x["returns"],
                _pct(x["returns"], x["units"] - x["cancels"]),
                x["why"].most_common(1)[0][0] if x["why"] else "",
            ]
        )
    units = sum(x["units"] for x in a.values())
    cancels = sum(x["cancels"] for x in a.values())
    rets = sum(x["returns"] for x in a.values())
    sheet.total = [
        "Итого",
        "",
        "",
        units,
        _num(sum(x["rub"] for x in a.values())),
        cancels,
        rets,
        _pct(rets, units - cancels),
        "",
    ]
    return sheet


def in_period(postings: list[dict], returns: list[dict], accruals: list[dict], start: date, end: date):
    """Только строки, чей московский день внутри периода: Ozon режет по UTC, края могут вылезти."""
    lo, hi = start.isoformat(), end.isoformat()
    return (
        [p for p in postings if lo <= msk_day(p.get("in_process_at", "")) <= hi],
        [r for r in returns if lo <= msk_day(r.get("logistic", {}).get("return_date", "")) <= hi],
        [x for x in accruals if lo <= str(x.get("date", ""))[:10] <= hi],
    )


def build(
    postings,
    returns,
    products,
    fbo,
    accruals,
    start: date,
    end: date,
    campaigns: list[dict] | None = None,
    ad_daily: list[dict] | None = None,
) -> list[Sheet]:
    """Листы отчёта; ad_daily=None — ключа рекламы нет, листа «Реклама» и столбцов расхода нет."""
    postings, returns, accruals = in_period(postings, returns, accruals, start, end)
    days = (end - start).days + 1
    if ad_daily is not None:
        lo, hi = start.isoformat(), end.isoformat()
        ad_daily = [r for r in ad_daily if lo <= str(r.get("date", ""))[:10] <= hi]
    sheets = [
        sales_sheet(postings, returns, accruals, start, end, ad_daily),
        stocks_sheet(products, fbo, postings, days),
    ]
    if ad_daily is not None:
        sheets.append(ads_sheet(campaigns or [], ad_daily))
    return [*sheets, items_sheet(postings, returns)]
