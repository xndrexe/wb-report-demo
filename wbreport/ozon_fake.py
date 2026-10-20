"""Выдуманный магазин на Ozon и местные серверы с адресами и форматом ответов Seller API и Performance API.

Нужен для демо и тестов: клиент ходит к ним по настоящему HTTP, как к Ozon.
Магазин «Уютный дом», товары, склады, возвраты, начисления и рекламные кампании — выдуманы.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from .fake import LocalServer
from .ozon_report import MSK

BRAND = "Уютный дом"
ITEMS = [  # артикул продавца, SKU Ozon, название, цена со скидкой, заказов в день
    ("PLAID-GREY-150", 1834500101, "Плед флисовый серый 150×200", 1890, 7),
    ("PLAID-BEIGE-200", 1834500102, "Плед вязаный бежевый 200×220", 2490, 4),
    ("PILLOW-50x70", 1834500103, "Подушка гипоаллергенная 50×70", 990, 11),
    ("TOWEL-SET-3", 1834500104, "Набор махровых полотенец, 3 шт", 1490, 6),
    ("BED-LINEN-EURO", 1834500105, "Постельное бельё евро, сатин", 3790, 3),
    ("TABLECLOTH-140", 1834500106, "Скатерть хлопковая 140×180", 1090, 2),
    ("BATHROBE-M", 1834500107, "Халат вафельный, размер M", 2790, 2),
]
CLUSTERS = ["Москва, МО и Дальние регионы", "Санкт-Петербург и СЗО", "Казань", "Краснодар"]
REASONS = ["Не подошёл размер", "Не понравился цвет", "Товар с браком", "Передумал покупать"]
CLIENT_ID = "demo-client"
API_KEY = "demo-key"
ADS_CLIENT_ID = "demo-ads@advertising.performance.ozon.ru"
ADS_SECRET = "demo-ads-secret"
# id, название, тип, состояние, оплата, старт и стоп (дней назад), расход в день, CTR %, конверсия %, чек
CAMPAIGNS = [
    (
        2140001,
        "Пледы — продвижение в поиске",
        "SKU",
        "CAMPAIGN_STATE_RUNNING",
        "CPC",
        60,
        0,
        2200,
        3.1,
        4.5,
        2100,
    ),
    (2140002, "Подушки и полотенца", "SKU", "CAMPAIGN_STATE_RUNNING", "CPC", 40, 0, 1300, 2.6, 5.5, 1150),
    (
        2140003,
        "Оплата за заказ — все товары",
        "SEARCH_PROMO",
        "CAMPAIGN_STATE_RUNNING",
        "CPO",
        90,
        0,
        1800,
        2.2,
        6.0,
        1900,
    ),
    (
        2140004,
        "Баннер «Осенний уют»",
        "BANNER",
        "CAMPAIGN_STATE_INACTIVE",
        "CPM",
        45,
        12,
        900,
        0.6,
        1.5,
        2600,
    ),
    (2140005, "Скатерти — тест", "SKU", "CAMPAIGN_STATE_STOPPED", "CPC", 35, 6, 450, 2.0, 3.0, 1090),
    (2140006, "Халаты — весна", "SKU", "CAMPAIGN_STATE_ARCHIVED", "CPC", 200, 150, 500, 2.0, 3.0, 2790),
]


def _z(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(schema: str, art: str, sku: int, name: str, price: float, qty: int) -> dict:
    """Товар в отправлении: у FBO цена строкой, у FBS (v4) — объектом."""
    p = {"offer_id": art, "sku": sku, "name": name, "quantity": qty}
    if schema == "FBO":
        return {**p, "price": f"{price:.4f}", "currency_code": "RUB"}
    return {**p, "price": {"amount": f"{price:.2f}", "currency": "RUB"}}


def generate(today: date, days: int = 45, seed: int = 11) -> dict:
    rnd = random.Random(seed)
    fbo, fbs, returns, accruals = [], [], [], []
    n = 0
    for back in range(days, -1, -1):
        day = today - timedelta(days=back)
        weekend = 1.2 if day.weekday() >= 5 else 1.0
        last_minute = 720 if day == today else 1439  # сегодняшний день — только до полудня
        for art, sku, name, price, rate in ITEMS:
            for _ in range(max(0, round(rnd.gauss(rate * weekend, rate * 0.3)))):
                n += 1
                at = datetime.combine(day, datetime.min.time(), MSK) + timedelta(
                    minutes=rnd.randint(0, last_minute)
                )
                schema = "FBO" if rnd.random() < 0.75 else "FBS"
                qty = 2 if rnd.random() < 0.08 else 1
                # статус по возрасту заказа в днях: старые доставлены, свежие ещё в пути
                if rnd.random() < 0.06:
                    status = "cancelled"
                elif back > 4:
                    status = "delivered"
                elif back > 1:
                    status = "delivering"
                else:
                    status = "awaiting_deliver"
                payout = round(price * qty * 0.78, 2)
                posting = {
                    "posting_number": f"{40000000 + n}-0001-1",
                    "order_number": f"{40000000 + n}-0001",
                    "order_id": 900000000 + n,
                    "status": status,
                    "in_process_at": _z(at),
                    "products": [_item(schema, art, sku, name, price, qty)],
                    "financial_data": {
                        "products": [
                            {
                                "product_id": sku,
                                "price": price,
                                "quantity": qty,
                                "payout": payout,
                                **(
                                    {
                                        "commission_amount": round(price * qty * 0.15, 2),
                                        "commission_percent": 15,
                                    }
                                    if schema == "FBO"
                                    else {
                                        "commission": {
                                            "amount": round(price * qty * 0.15, 2),
                                            "currency": "RUB",
                                            "percent": 15,
                                        }
                                    }
                                ),
                            }
                        ]
                    },
                }
                if schema == "FBO":
                    fbo.append({**posting, "created_at": _z(at)})
                else:
                    fbs.append(posting)
                if status == "cancelled":
                    if schema == "FBS" and rnd.random() < 0.5:  # отмена тоже бывает в списке возвратов
                        returns.append(_return(rnd, posting, "Cancellation", at + timedelta(hours=5), schema))
                    continue
                if status != "delivered":
                    continue
                got = (at + timedelta(days=3)).date()
                accruals.append(
                    {
                        "accrued_category": "POSTING",
                        "date": got.isoformat(),
                        "unit_number": posting["posting_number"],
                        "total_amount": {"amount": f"{payout:.2f}", "currency": "RUB"},
                        "posting": {"delivery_schema": schema},
                    }
                )
                roll = rnd.random()
                kind = "ClientReturn" if roll < 0.04 else "FullReturn" if roll < 0.06 else ""
                back_at = at + timedelta(days=rnd.randint(4, 9), hours=rnd.randint(0, 10))
                if kind and back_at.date() <= today:
                    returns.append(_return(rnd, posting, kind, back_at, schema))
        # хранение на складах Ozon — по товарам, раз в день; продвижение — без привязки к товару
        if day < today:
            for _art, sku, *_ in ITEMS:
                accruals.append(
                    {
                        "accrued_category": "ITEM",
                        "date": day.isoformat(),
                        "item_fees": {"fees": [{"sku": sku}]},
                        "total_amount": {"amount": f"-{rnd.randint(8, 25)}.00", "currency": "RUB"},
                    }
                )
            if day.weekday() == 0:
                accruals.append(
                    {
                        "accrued_category": "NON_ITEM",
                        "date": day.isoformat(),
                        "total_amount": {"amount": "-1500.00", "currency": "RUB"},
                    }
                )
    for i, a in enumerate(accruals, start=1):
        a["accrual_id"] = 7000000 + i

    products, stocks = [], []
    for art, sku, name, _price, rate in ITEMS:
        # подушки почти кончились — в отчёте видно, что пора везти поставку
        top = rate * 2 if art.startswith("PILLOW") else rate * 10
        at_ozon = 0
        for cluster in CLUSTERS:
            q = rnd.randint(0, top)
            at_ozon += q
            stocks.append(
                {
                    "sku": sku,
                    "offer_id": art,
                    "name": name,
                    "cluster_name": cluster,
                    "warehouse_name": f"{cluster.split(',')[0].upper()}_РФЦ",
                    "available_stock_count": q,
                    "transit_stock_count": rnd.choice([0, 0, rate * 5]),
                    "return_from_customer_stock_count": rnd.randint(0, 2),
                    "ads": round(rate * 0.9, 1),
                    "idc": round(q / max(rate, 1), 1),
                }
            )
        own = 0 if art.startswith("PILLOW") else rnd.randint(0, rate * 4)
        products.append(
            {
                "offer_id": art,
                "product_id": 500000 + sku % 1000,
                "stocks": [
                    {"type": "fbo", "sku": sku, "present": at_ozon, "reserved": rnd.randint(0, 3)},
                    {"type": "fbs", "sku": sku, "present": own, "reserved": min(own, rnd.randint(0, 2))},
                ],
            }
        )
    return {
        "fbo": fbo,
        "fbs": fbs,
        "returns": returns,
        "accruals": accruals,
        "products": products,
        "stocks": stocks,
    }


def _money(v: float) -> str:
    """Сумма как в Performance API: строкой, с запятой."""
    return f"{v:.2f}".replace(".", ",")


def generate_ads(today: date, days: int = 45, seed: int = 12) -> dict:
    """Кампании и их дневная статистика (строка на кампанию × день, как /statistics/daily/json)."""
    rnd = random.Random(seed)
    campaigns, daily = [], []
    for cid, title, kind, state, pay, start, stop, spend, ctr, conv, check in CAMPAIGNS:
        campaigns.append(
            {
                "id": str(cid),
                "title": title,
                "advObjectType": kind,
                "state": state,
                "paymentType": pay,
                "fromDate": (today - timedelta(days=start)).isoformat(),
                "toDate": "",
                "dailyBudget": str(spend * 2 * 1_000_000),  # бюджеты — в миллионных долях рубля
                "placement": ["PLACEMENT_SEARCH_AND_CATEGORY"],
            }
        )
        for back in range(min(start, days), -1, -1):
            day = today - timedelta(days=back)
            off = back < stop
            if off and back < stop - 3:  # после остановки Ozon ещё пару дней отдаёт нулевые строки
                continue
            share = 0.5 if day == today else 1.0
            spent = 0.0 if off else max(0.0, rnd.gauss(spend, spend * 0.25)) * share
            clicks = round(spent / max(spend / 120, 1)) if spent else 0
            views = round(clicks * 100 / ctr) if clicks else 0
            orders = sum(rnd.random() < conv / 100 for _ in range(clicks))
            daily.append(
                {
                    "id": str(cid),
                    "title": title,
                    "date": day.isoformat(),
                    "views": str(views),
                    "clicks": str(clicks),
                    "moneySpent": _money(spent),
                    "avgBid": _money(spent / clicks if clicks else 0),
                    "orders": str(orders),
                    "ordersMoney": _money(orders * check * rnd.uniform(0.9, 1.1)),
                }
            )
    return {"campaigns": campaigns, "daily": daily}


def _return(rnd: random.Random, posting: dict, kind: str, at: datetime, schema: str) -> dict:
    item = posting["products"][0]
    price = item["price"]["amount"] if isinstance(item["price"], dict) else item["price"]
    return {
        "type": kind,
        "schema": schema,
        "posting_number": posting["posting_number"],
        "order_number": posting["order_number"],
        "return_reason_name": rnd.choice(REASONS) if kind != "Cancellation" else "Покупатель отменил заказ",
        "product": {
            "sku": item["sku"],
            "offer_id": item["offer_id"],
            "name": item["name"],
            "quantity": item["quantity"],
            "price": {"price": float(price), "currency_code": "RUB"},
        },
        "logistic": {"return_date": _z(at)},
        "visual": {"status": {"sys_name": "ReturnedToSeller", "display_name": "Возвращён продавцу"}},
    }


class FakeOzon(LocalServer):
    """Местный сервер Ozon. fail — словарь путь → список кодов, которые отдать перед успехом."""

    PAGE = {"/v4/product/info/stocks": 1000, "/v1/finance/accrual/by-day": 50}

    def __init__(self, data: dict, fail: dict[str, list[int]] | None = None):
        self.data = data
        for i, r in enumerate(sorted(data["returns"], key=lambda r: r["logistic"]["return_date"]), start=1):
            r.setdefault("id", 300000 + i)
        self.fail = fail or {}
        self.calls: list[tuple[str, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                fake._handle(self)

        self._listen(Handler)

    def _handle(self, h) -> None:
        path = h.path
        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length", 0))) or b"{}")
        self.calls.append((path, body))
        if h.headers.get("Client-Id") != CLIENT_ID or h.headers.get("Api-Key") != API_KEY:
            return self._reply(h, 401, {"code": 16, "message": "Invalid Api-Key, please contact support"})
        if self.fail.get(path):
            return self._reply(h, self.fail[path].pop(0), {"code": 8, "message": "rate limit"})
        f = body.get("filter", {})
        if path == "/v2/posting/fbo/list":
            rows = [p for p in self.data["fbo"] if f["since"] <= p["in_process_at"] <= f["to"]]
            rows.sort(key=lambda p: p["in_process_at"])
            off, lim = body.get("offset", 0), body["limit"]
            if off > 20000:
                return self._reply(h, 400, {"code": 3, "message": "offset is too big"})
            return self._reply(h, 200, {"result": rows[off : off + lim]})
        if path == "/v4/posting/fbs/list":
            if body["limit"] > 100:
                return self._reply(h, 400, {"code": 3, "message": "limit must be <= 100"})
            rows = [p for p in self.data["fbs"] if f["since"] <= p["in_process_at"] <= f["to"]]
            return self._reply(h, 200, self._cursor_page(rows, body, "postings"))
        if path == "/v4/product/info/stocks":
            return self._reply(h, 200, self._cursor_page(self.data["products"], body, "items"))
        if path == "/v1/analytics/stocks":
            skus = body.get("skus") or []
            if not skus or len(skus) > 100:
                return self._reply(h, 400, {"code": 3, "message": "skus: from 1 to 100 values"})
            return self._reply(h, 200, {"items": [s for s in self.data["stocks"] if str(s["sku"]) in skus]})
        if path == "/v1/returns/list":
            span = f["logistic_return_date"]
            rows = sorted(
                (
                    r
                    for r in self.data["returns"]
                    if span["time_from"] <= r["logistic"]["return_date"] <= span["time_to"]
                    and r["id"] > body.get("last_id", 0)
                ),
                key=lambda r: r["id"],
            )
            lim = body["limit"]
            return self._reply(h, 200, {"returns": rows[:lim], "has_next": len(rows) > lim})
        if path == "/v1/finance/accrual/by-day":
            rows = [a for a in self.data["accruals"] if a["date"] == body["date"]]
            after = int(body.get("last_id") or 0)
            page = [a for a in rows if a["accrual_id"] > after][: self.PAGE[path]]
            return self._reply(
                h, 200, {"accruals": page, "last_id": str(page[-1]["accrual_id"]) if page else ""}
            )
        return self._reply(h, 404, {"code": 5, "message": "not found"})

    def _cursor_page(self, rows: list[dict], body: dict, key: str) -> dict:
        start = int(body.get("cursor") or 0)
        end = start + body["limit"]
        return {
            key: rows[start:end],
            "has_next": end < len(rows),
            "cursor": str(end) if end < len(rows) else "",
        }


class FakePerformance(LocalServer):
    """Местный сервер Performance API: токен, кампании, дневная статистика. Лимит — 62 дня за выгрузку."""

    def __init__(self, data: dict, fail: dict[str, list[int]] | None = None, expires_in: int = 1800):
        self.data = data
        self.fail = fail or {}
        self.expires_in = expires_in
        self.tokens: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                fake._handle(self, self.path, body)

            def do_GET(self):
                url = urlsplit(self.path)
                fake._handle(self, url.path, {k: v[0] for k, v in parse_qs(url.query).items()})

        self._listen(Handler)

    def revoke(self) -> None:
        """Отозвать выданные токены раньше срока — клиент должен взять новый."""
        self.tokens.clear()

    def _handle(self, h, path: str, body: dict) -> None:
        self.calls.append((path, body))
        if self.fail.get(path):
            return self._reply(h, self.fail[path].pop(0), {"error": "too many requests"})
        if path == "/api/client/token":
            if (body.get("client_id"), body.get("client_secret")) != (ADS_CLIENT_ID, ADS_SECRET) or body.get(
                "grant_type"
            ) != "client_credentials":
                return self._reply(h, 401, {"error": "invalid_client"})
            token = f"demo-token-{len(self.calls)}"
            self.tokens.add(token)
            return self._reply(
                h, 200, {"access_token": token, "expires_in": self.expires_in, "token_type": "Bearer"}
            )
        if h.headers.get("Authorization", "").removeprefix("Bearer ") not in self.tokens:
            return self._reply(h, 401, {"code": 16, "message": "Запрос не аутентифицирован"})
        if path == "/api/client/campaign":
            return self._reply(h, 200, {"list": self.data["campaigns"]})
        if path == "/api/client/statistics/daily/json":
            lo, hi = body.get("dateFrom", ""), body.get("dateTo", "")
            if (date.fromisoformat(hi) - date.fromisoformat(lo)).days + 1 > 62:
                return self._reply(h, 400, {"code": 3, "message": "date range is too long"})
            return self._reply(h, 200, {"rows": [r for r in self.data["daily"] if lo <= r["date"] <= hi]})
        return self._reply(h, 404, {"code": 5, "message": "not found"})
