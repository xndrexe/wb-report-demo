"""Выдуманный магазин на Ozon и местный сервер с теми же адресами и форматом ответов, что у Seller API.

Нужен для демо и тестов: клиент ходит к нему по настоящему HTTP, как к Ozon.
Магазин «Уютный дом», товары, склады, возвраты и начисления — выдуманы.
"""

from __future__ import annotations

import json
import random
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
                age = back
                status = (
                    "cancelled"
                    if rnd.random() < 0.06
                    else "delivered"
                    if age > 4
                    else "delivering"
                    if age > 1
                    else "awaiting_deliver"
                )
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


class FakeOzon:
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

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def _reply(self, h, code: int, body) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        if code == 429:
            h.send_header("Retry-After", "1")
        h.end_headers()
        h.wfile.write(raw)

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
