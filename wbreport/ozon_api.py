"""Клиент Ozon Seller API: отправления FBO и FBS, остатки, возвраты, начисления.

Методы и поля — по схеме Seller API (docs.ozon.ru/api/seller, версия осени 2026):
- /v2/posting/fbo/list — отправления со складов Ozon, до 1000 за ответ, offset не больше 20 000;
- /v4/posting/fbs/list — отправления со своего склада, до 100 за ответ, листание курсором
  (/v3 отключён летом 2026);
- /v4/product/info/stocks — все товары кабинета и остатки на своём складе;
- /v1/analytics/stocks — остатки на складах Ozon по кластерам, до 100 SKU за запрос;
- /v1/returns/list — возвраты FBO и FBS, до 500 за ответ, листание по last_id;
- /v1/finance/accrual/by-day — начисления за день (замена /v3/finance/transaction/list,
  отключённого летом 2026).
Все даты Ozon — в UTC с «Z» на конце.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import requests

log = logging.getLogger(__name__)

FBO_PAGE = 1000
FBO_MAX_OFFSET = 20_000  # дальше Ozon не листает — поэтому период режем на недели
FBS_PAGE = 100
RETURNS_PAGE = 500
SKU_BATCH = 100
CHUNK_DAYS = 7


class OzonError(RuntimeError):
    """Ответ API, с которым дальше идти нельзя: неверный ключ, нет прав, сбой Ozon."""


def _z(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class OzonClient:
    def __init__(
        self,
        client_id: str,
        api_key: str,
        *,
        url: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.url = url
        self.session = session or requests.Session()
        self.session.headers.update({"Client-Id": client_id, "Api-Key": api_key})
        self.sleep = sleep

    def _post(self, path: str, body: dict, attempts: int = 4) -> dict:
        for attempt in range(1, attempts + 1):
            resp = self.session.post(self.url + path, json=body, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = float(resp.headers.get("Retry-After", 0) or 0) or min(60, 5 * attempt)
                log.warning(
                    "%s -> %s, retry in %.0fs (%d/%d)", path, resp.status_code, delay, attempt, attempts
                )
                if attempt == attempts:
                    break
                self.sleep(delay)
                continue
            if resp.status_code in (401, 403):
                raise OzonError(
                    f"{path}: доступ запрещён ({resp.status_code}) — проверьте Client-Id и API-ключ"
                )
            if resp.status_code >= 400:
                raise OzonError(f"{path}: ошибка {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise OzonError(f"{path}: Ozon не ответил за {attempts} попытки")

    @staticmethod
    def _chunks(since: datetime, to: datetime) -> Iterator[tuple[datetime, datetime]]:
        start = since
        while start < to:
            end = min(start + timedelta(days=CHUNK_DAYS), to)
            yield start, end
            start = end

    def fbo_postings(self, since: datetime, to: datetime) -> list[dict]:
        """Отправления FBO; неделя за неделей, чтобы не упереться в потолок offset."""
        rows: dict[str, dict] = {}
        for lo, hi in self._chunks(since, to):
            offset = 0
            while True:
                page = self._post(
                    "/v2/posting/fbo/list",
                    {
                        "dir": "ASC",
                        "filter": {"since": _z(lo), "to": _z(hi)},
                        "limit": FBO_PAGE,
                        "offset": offset,
                        "with": {"financial_data": True},
                    },
                )["result"]
                for p in page:  # граница недель попадает в оба куска — дубли убирает ключ
                    rows[p["posting_number"]] = {**p, "delivery_schema": "FBO"}
                offset += FBO_PAGE
                if len(page) < FBO_PAGE:
                    break
                if offset > FBO_MAX_OFFSET:
                    log.warning("fbo %s..%s: more than %d postings, rest skipped", lo, hi, FBO_MAX_OFFSET)
                    break
        return list(rows.values())

    def fbs_postings(self, since: datetime, to: datetime) -> list[dict]:
        rows: dict[str, dict] = {}
        cursor = ""
        while True:
            body = {
                "filter": {"since": _z(since), "to": _z(to)},
                "limit": FBS_PAGE,
                "with": {"financial_data": True},
            }
            if cursor:
                body["cursor"] = cursor
            page = self._post("/v4/posting/fbs/list", body)
            for p in page.get("postings") or []:
                rows[p["posting_number"]] = {**p, "delivery_schema": "FBS"}
            cursor = page.get("cursor") or ""
            if not page.get("has_next") or not cursor:
                return list(rows.values())

    def products(self) -> list[dict]:
        """Все товары кабинета (кроме архива) с SKU и остатками на своём складе."""
        items: list[dict] = []
        cursor = ""
        while True:
            body = {"filter": {"visibility": "ALL"}, "limit": 1000}
            if cursor:
                body["cursor"] = cursor
            page = self._post("/v4/product/info/stocks", body)
            items.extend(page.get("items") or [])
            cursor = page.get("cursor") or ""
            if not page.get("items") or not cursor:
                return items

    def fbo_stocks(self, skus: list[int]) -> list[dict]:
        """Остатки на складах Ozon: строка на товар × склад."""
        rows: list[dict] = []
        for i in range(0, len(skus), SKU_BATCH):
            batch = [str(s) for s in skus[i : i + SKU_BATCH]]
            rows.extend(self._post("/v1/analytics/stocks", {"skus": batch}).get("items") or [])
        return rows

    def returns(self, since: datetime, to: datetime) -> list[dict]:
        """Возвраты, оформленные за период (по дате создания возврата)."""
        rows: list[dict] = []
        last_id = 0
        while True:
            page = self._post(
                "/v1/returns/list",
                {
                    "filter": {"logistic_return_date": {"time_from": _z(since), "time_to": _z(to)}},
                    "limit": RETURNS_PAGE,
                    "last_id": last_id,
                },
            )
            got = page.get("returns") or []
            rows.extend(got)
            if not page.get("has_next") or not got:
                return rows
            last_id = got[-1]["id"]

    def accruals(self, day: date) -> list[dict]:
        """Начисления за один день: продажи, комиссии, доставка, услуги."""
        rows: list[dict] = []
        last_id = ""
        while True:
            page = self._post("/v1/finance/accrual/by-day", {"date": day.isoformat(), "last_id": last_id})
            got = page.get("accruals") or []
            rows.extend(got)
            nxt = page.get("last_id") or ""
            if not got or not nxt or nxt == last_id:
                return rows
            last_id = nxt
