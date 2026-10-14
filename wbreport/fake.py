"""Выдуманный магазин на WB и местный сервер с теми же адресами и форматом ответов, что у API.

Нужен для демо и тестов: клиент ходит к нему по настоящему HTTP, как к WB.
Магазин «Уютный дом», товары, склады и цифры — выдуманы.
"""

from __future__ import annotations

import json
import random
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import api

BRAND = "Уютный дом"
ITEMS = [  # артикул продавца, артикул WB, предмет, цена до скидки, заказов в день
    ("PLAID-GREY-150", 210450001, "Пледы", 2490, 9),
    ("PLAID-BEIGE-200", 210450002, "Пледы", 3190, 6),
    ("PILLOW-50x70", 210450003, "Подушки", 1290, 14),
    ("TOWEL-SET-3", 210450004, "Полотенца", 1890, 8),
    ("BED-LINEN-EURO", 210450005, "Комплекты постельного белья", 4590, 4),
    ("TABLECLOTH-140", 210450006, "Скатерти", 1390, 3),
    ("CURTAIN-LINEN", 210450007, "Шторы", 2790, 2),
    ("BATHROBE-M", 210450008, "Халаты", 3490, 3),
]
WAREHOUSES = ["Коледино", "Электросталь", "Казань", "Краснодар"]
CAMPAIGNS = [
    (30017001, "Пледы — поиск", 9),
    (30017002, "Подушки — авто", 8),
    (30017003, "Новинки — карточка", 5),
]
TOKEN = "demo-token"


def generate(today: date, days: int = 45, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    orders, sales, ads = [], [], []
    n = 0
    for back in range(days, -1, -1):
        day = today - timedelta(days=back)
        weekend = 1.25 if day.weekday() >= 5 else 1.0
        last_minute = 720 if day == today else 1439  # сегодняшний день — только до полудня
        for art, nm, subject, price, rate in ITEMS:
            for _ in range(max(0, round(rnd.gauss(rate * weekend, rate * 0.3)))):
                n += 1
                ts = datetime.combine(day, datetime.min.time()) + timedelta(
                    minutes=rnd.randint(0, last_minute)
                )
                discount = rnd.choice([20, 25, 30])
                pwd = round(price * (1 - discount / 100), 2)
                cancel = rnd.random() < 0.07
                base = {
                    "date": ts.isoformat(),
                    "lastChangeDate": ts.isoformat(),
                    "warehouseName": rnd.choice(WAREHOUSES),
                    "warehouseType": "Склад WB",
                    "countryName": "Россия",
                    "supplierArticle": art,
                    "nmId": nm,
                    "barcode": f"46{nm}",
                    "category": "Дом",
                    "subject": subject,
                    "brand": BRAND,
                    "techSize": "0",
                    "totalPrice": price,
                    "discountPercent": discount,
                    "spp": 15,
                    "priceWithDisc": pwd,
                    "finishedPrice": round(pwd * 0.85, 2),
                    "srid": f"demo{n:07d}",
                    "gNumber": f"g{n:07d}",
                }
                orders.append(
                    {
                        **base,
                        "isCancel": cancel,
                        "cancelDate": (ts + timedelta(hours=3)).isoformat()
                        if cancel
                        else "0001-01-01T00:00:00",
                    }
                )
                sold = ts + timedelta(days=rnd.randint(2, 6), minutes=rnd.randint(0, 600))
                if cancel or sold.date() >= today or rnd.random() > 0.82:
                    continue
                sales.append(
                    {
                        **base,
                        "date": sold.isoformat(),
                        "lastChangeDate": sold.isoformat(),
                        "saleID": f"S{n:010d}",
                        "forPay": round(pwd * 0.76, 2),
                    }
                )
                back_at = sold + timedelta(days=rnd.randint(1, 4))
                if rnd.random() < 0.05 and back_at.date() < today:
                    sales.append(
                        {
                            **base,
                            "date": back_at.isoformat(),
                            "lastChangeDate": back_at.isoformat(),
                            "saleID": f"R{n:010d}",
                            "priceWithDisc": -pwd,
                            "finishedPrice": -round(pwd * 0.85, 2),
                            "forPay": -round(pwd * 0.76, 2),
                        }
                    )
        for cid, name, kind in CAMPAIGNS:
            for _ in range(rnd.randint(1, 3)):
                hour = rnd.randint(8, last_minute // 60 - 1)
                ads.append(
                    {
                        "updNum": 0,
                        "updTime": f"{day}T{hour:02d}:{rnd.randint(0, 59):02d}:00",
                        "updSum": rnd.choice([300, 500, 700, 1000]),
                        "advertId": cid,
                        "campName": name,
                        "advertType": kind,
                        "paymentType": "Баланс",
                        "advertStatus": 9,
                    }
                )
    stocks = []
    for art, nm, subject, _price, rate in ITEMS:
        # подушки почти кончились — в отчёте видно, что пора везти поставку
        top = rate * 2 if art.startswith("PILLOW") else rate * 12
        whs = [{"warehouseName": w, "quantity": rnd.randint(0, top)} for w in WAREHOUSES]
        whs = [w for w in whs if w["quantity"]]  # WB отдаёт только ненулевые
        total = sum(w["quantity"] for w in whs)
        whs += [
            {"warehouseName": "В пути до получателей", "quantity": rnd.randint(rate, rate * 3)},
            {"warehouseName": "В пути возвраты на склад WB", "quantity": rnd.randint(0, 3)},
            {"warehouseName": "Всего находится на складах", "quantity": total},
        ]
        stocks.append(
            {
                "brand": BRAND,
                "subjectName": subject,
                "vendorCode": art,
                "nmId": nm,
                "barcode": f"46{nm}",
                "techSize": "0",
                "volume": 3.2,
                "warehouses": whs,
            }
        )
    return {"orders": orders, "sales": sales, "ads": ads, "stocks": stocks}


class FakeWB:
    """Местный сервер WB. fail — словарь путь → список кодов, которые отдать перед успехом."""

    def __init__(self, data: dict, fail: dict[str, list[int]] | None = None):
        self.data = data
        self.fail = fail or {}
        self.calls: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake._handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self._polls = 0

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
            h.send_header("X-Ratelimit-Retry", "1")
        h.end_headers()
        h.wfile.write(raw)

    def _handle(self, h) -> None:
        u = urlparse(h.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append(u.path)
        if h.headers.get("Authorization") != TOKEN:
            return self._reply(h, 401, {"title": "unauthorized", "detail": "token is invalid"})
        if self.fail.get(u.path):
            return self._reply(h, self.fail[u.path].pop(0), {"title": "error"})
        if u.path in ("/api/v1/supplier/orders", "/api/v1/supplier/sales"):
            key = "orders" if u.path.endswith("orders") else "sales"
            rows = sorted(
                (r for r in self.data[key] if r["lastChangeDate"] >= q["dateFrom"]),
                key=lambda r: r["lastChangeDate"],
            )
            return self._reply(h, 200, rows[: api.PAGE_ROWS])  # как WB: длинный ответ режется
        if u.path == "/api/v1/warehouse_remains":
            self._polls = 0
            return self._reply(h, 200, {"data": {"taskId": "task-1"}})
        if u.path == "/api/v1/warehouse_remains/tasks/task-1/status":
            self._polls += 1
            return self._reply(
                h, 200, {"data": {"id": "task-1", "status": "done" if self._polls > 1 else "processing"}}
            )
        if u.path == "/api/v1/warehouse_remains/tasks/task-1/download":
            return self._reply(h, 200, self.data["stocks"])
        if u.path == "/adv/v1/upd":
            rows = [a for a in self.data["ads"] if q["from"] <= a["updTime"][:10] <= q["to"]]
            return self._reply(h, 200, rows)
        return self._reply(h, 404, {"title": "not found"})
