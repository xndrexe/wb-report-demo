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

Реклама — отдельный Performance API (api-performance.ozon.ru, документация версии 2.0, 2026) со своим
ключом: client_id и client_secret сервисного аккаунта, по ним токен на 30 минут (/api/client/token).
- /api/client/campaign — список кампаний: название, тип, состояние;
- /api/client/statistics/daily/json — по кампании за день: показы, клики, расход, заказы штуками и
  рублями; суммы строкой с запятой («2764,63»), дни — московские; не больше 62 дней за выгрузку.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta

import requests

from .api import retry_delay

log = logging.getLogger(__name__)

FBO_PAGE = 1000
FBO_MAX_OFFSET = 20_000  # дальше Ozon не листает — поэтому период режем на недели
FBS_PAGE = 100
RETURNS_PAGE = 500
SKU_BATCH = 100
CHUNK_DAYS = 7
AD_MAX_DAYS = 62  # лимит Performance API на выгрузку статистики
TOKEN_MARGIN = 60  # новый токен — за минуту до истечения старого


class OzonError(RuntimeError):
    """Ответ API, с которым дальше идти нельзя: неверный ключ, нет прав, сбой Ozon."""


def _z(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _send(call: Callable[[], requests.Response], path: str, sleep, attempts: int) -> requests.Response:
    """Запрос с повтором на 429 и сбоях Ozon; пауза — из Retry-After или своя, растущая."""
    for attempt in range(1, attempts + 1):
        resp = call()
        if resp.status_code != 429 and resp.status_code < 500:
            return resp
        delay = retry_delay(resp.headers.get("Retry-After"), attempt)
        log.warning("%s -> %s, retry in %.0fs (%d/%d)", path, resp.status_code, delay, attempt, attempts)
        if attempt < attempts:
            sleep(delay)
    raise OzonError(f"{path}: Ozon не ответил за {attempts} попытки")


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
        resp = _send(
            lambda: self.session.post(self.url + path, json=body, timeout=60), path, self.sleep, attempts
        )
        if resp.status_code in (401, 403):
            raise OzonError(f"{path}: доступ запрещён ({resp.status_code}) — проверьте Client-Id и API-ключ")
        if resp.status_code >= 400:
            raise OzonError(f"{path}: ошибка {resp.status_code}: {resp.text[:200]}")
        return resp.json()

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
        """Возвраты за период — по дате возврата на склад (logistic_return_date), не по дате заявки."""
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


class OzonAdsClient:
    """Performance API: кампании и их дневная статистика. Только чтение."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        url: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url = url
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self._token = ""
        self._expires = 0.0

    def _auth(self) -> str:
        if self._token and self.clock() < self._expires - TOKEN_MARGIN:
            return self._token
        path = "/api/client/token"
        body = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        resp = _send(lambda: self.session.post(self.url + path, json=body, timeout=60), path, self.sleep, 4)
        if resp.status_code >= 400:
            raise OzonError(
                f"{path}: ключ рекламы не принят ({resp.status_code}) — проверьте client_id и client_secret"
                " Performance API"
            )
        data = resp.json()
        self._token = data["access_token"]
        self._expires = self.clock() + float(data.get("expires_in") or 1800)
        return self._token

    def _get(self, path: str, params: dict | None = None):
        for fresh in (False, True):  # токен могли отозвать раньше срока — один раз берём новый
            if fresh:
                self._token = ""
            headers = {"Authorization": f"Bearer {self._auth()}"}
            resp = _send(
                lambda h=headers: self.session.get(self.url + path, params=params, headers=h, timeout=120),
                path,
                self.sleep,
                4,
            )
            if resp.status_code not in (401, 403):
                break
        if resp.status_code in (401, 403):
            raise OzonError(
                f"{path}: доступ к рекламе запрещён ({resp.status_code}) — проверьте ключ Performance API"
            )
        if resp.status_code >= 400:
            raise OzonError(f"{path}: ошибка {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def campaigns(self) -> list[dict]:
        """Все кампании кабинета, включая остановленные и архивные: расход за период мог быть и у них."""
        return self._get("/api/client/campaign").get("list") or []

    def daily(self, date_from: date, date_to: date) -> list[dict]:
        """Строка на кампанию × день; длинный период режется на куски по 62 дня."""
        rows: list[dict] = []
        start = date_from
        while start <= date_to:
            end = min(start + timedelta(days=AD_MAX_DAYS - 1), date_to)
            page = self._get(
                "/api/client/statistics/daily/json",
                {"dateFrom": start.isoformat(), "dateTo": end.isoformat()},
            )
            rows.extend(page.get("rows") or [])
            start = end + timedelta(days=1)
        return rows
