"""Клиент официального API Wildberries: заказы, продажи, остатки, расходы на рекламу.

Методы и лимиты — по документации dev.wildberries.ru (раздел «Отчёты» и «Продвижение»):
- /api/v1/supplier/orders и /sales — 1 запрос в минуту, до ~80 000 строк за ответ;
- остатки — отчёт-задание /api/v1/warehouse_remains: создать, дождаться, скачать;
- /adv/v1/upd — фактические затраты на рекламу, период не длиннее 31 дня.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)

PAGE_ROWS = 80_000  # условный потолок строк одного ответа orders/sales
MAX_RETRY_DELAY = 300.0  # дольше пяти минут не ждём, что бы ни просил сервер


def retry_delay(header: str | None, attempt: int) -> float:
    """Пауза перед повтором из заголовка ответа; мусор, «inf», «nan» и минус — своя растущая пауза."""
    try:
        delay = float(header or 0)
    except ValueError:
        delay = 0.0
    if not 0 < delay < float("inf"):  # nan сюда тоже не пройдёт
        delay = min(60, 5 * attempt)
    return min(delay, MAX_RETRY_DELAY)


class WBError(RuntimeError):
    """Ответ API, с которым дальше идти нельзя: неверный токен, нет прав, сбой WB."""


class WBClient:
    def __init__(
        self,
        token: str,
        *,
        stats_url: str,
        analytics_url: str,
        advert_url: str,
        session: requests.Session | None = None,
        min_interval: float = 61.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """min_interval — пауза между запросами к одному методу (лимит WB — раз в минуту)."""
        self.urls = {"stats": stats_url, "analytics": analytics_url, "advert": advert_url}
        self.session = session or requests.Session()
        self.session.headers["Authorization"] = token
        self.min_interval = min_interval
        self.sleep = sleep
        self._last_call: dict[str, float] = {}

    def _get(self, api: str, path: str, params: dict | None = None, attempts: int = 4):
        url = self.urls[api] + path
        key = path.split("/tasks/")[0]  # у заданий остатков свой лимит на создание
        wait = self._last_call.get(key, -1e9) + self.min_interval - time.monotonic()
        if wait > 0 and key.endswith(("orders", "sales")):
            self.sleep(wait)
        for attempt in range(1, attempts + 1):
            resp = self.session.get(url, params=params, timeout=60)
            self._last_call[key] = time.monotonic()
            if resp.status_code == 429 or resp.status_code >= 500:
                # WB подсказывает, сколько ждать, в X-Ratelimit-Retry
                delay = retry_delay(resp.headers.get("X-Ratelimit-Retry"), attempt)
                log.warning(
                    "%s -> %s, retry in %.0fs (%d/%d)", path, resp.status_code, delay, attempt, attempts
                )
                if attempt == attempts:
                    break
                self.sleep(delay)
                continue
            if resp.status_code in (401, 403):
                raise WBError(f"{path}: доступ запрещён ({resp.status_code}) — проверьте токен и его права")
            if resp.status_code >= 400:
                raise WBError(f"{path}: ошибка {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise WBError(f"{path}: WB не ответил за {attempts} попытки")

    def _paged(self, path: str, date_from: date, key: str) -> list[dict]:
        """orders/sales: следующую страницу просим с lastChangeDate последней строки.

        Граничная строка приходит дважды — дубли убираем по key (srid или saleID).
        """
        rows: dict[str, dict] = {}
        cursor = date_from.isoformat()
        while True:
            page = self._get("stats", path, {"dateFrom": cursor, "flag": 0})
            for r in page:
                rows[r[key]] = r
            if len(page) < PAGE_ROWS or page[-1]["lastChangeDate"] == cursor:
                return list(rows.values())
            cursor = page[-1]["lastChangeDate"]

    def orders(self, date_from: date) -> list[dict]:
        return self._paged("/api/v1/supplier/orders", date_from, "srid")

    def sales(self, date_from: date) -> list[dict]:
        return self._paged("/api/v1/supplier/sales", date_from, "saleID")

    def stocks(self, poll: float = 5.0, timeout: float = 300.0) -> list[dict]:
        """Остатки по складам: отчёт-задание с разбивкой по артикулам."""
        task = self._get(
            "analytics",
            "/api/v1/warehouse_remains",
            {"groupByNm": "true", "groupBySa": "true", "locale": "ru"},
        )
        # id задания вставляется в путь: экранируем, чтобы «../» или «?» из ответа не сменили адрес
        task_id = quote(str(task["data"]["taskId"]), safe="")
        waited = 0.0
        while True:
            status = self._get("analytics", f"/api/v1/warehouse_remains/tasks/{task_id}/status")["data"][
                "status"
            ]
            if status == "done":
                return self._get("analytics", f"/api/v1/warehouse_remains/tasks/{task_id}/download")
            if status in ("canceled", "purged"):
                raise WBError(f"отчёт об остатках {task_id}: {status}")
            if waited >= timeout:
                raise WBError(f"отчёт об остатках {task_id} не готов за {timeout:.0f} с")
            self.sleep(poll)
            waited += poll

    def ad_costs(self, date_from: date, date_to: date) -> list[dict]:
        """Затраты на рекламу; WB отдаёт не больше 31 дня за запрос — режем период."""
        rows: list[dict] = []
        start = date_from
        while start <= date_to:
            end = min(start + timedelta(days=30), date_to)
            rows.extend(
                self._get("advert", "/adv/v1/upd", {"from": start.isoformat(), "to": end.isoformat()}) or []
            )
            start = end + timedelta(days=1)
        return rows
