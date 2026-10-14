"""Клиент WB против местного сервера с форматом ответов WB — настоящий HTTP, без интернета."""

from datetime import date

import pytest

from wbreport import api, fake
from wbreport.api import WBClient, WBError

TODAY = date(2026, 10, 14)


@pytest.fixture
def data():
    return fake.generate(TODAY, days=45)


def client(srv, token=fake.TOKEN, sleeps=None):
    return WBClient(
        token,
        stats_url=srv.url,
        analytics_url=srv.url,
        advert_url=srv.url,
        min_interval=0,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def test_orders_and_sales_since_date(data):
    with fake.FakeWB(data) as srv:
        c = client(srv)
        orders = c.orders(date(2026, 10, 1))
        sales = c.sales(date(2026, 10, 1))
    assert orders and all(o["lastChangeDate"] >= "2026-10-01" for o in orders)
    assert {s["saleID"][0] for s in sales} == {"S", "R"}


def test_pagination_uses_last_change_date(data, monkeypatch):
    monkeypatch.setattr(api, "PAGE_ROWS", 500)
    with fake.FakeWB(data) as srv:
        rows = client(srv).orders(date(2026, 9, 1))
        pages = srv.calls.count("/api/v1/supplier/orders")
    assert pages >= 3  # 45 дней заказов не влезают в страницу по 500 строк
    expected = {o["srid"] for o in data["orders"] if o["lastChangeDate"] >= "2026-09-01"}
    assert {r["srid"] for r in rows} == expected and len(rows) == len(expected)  # без дублей на стыках


def test_rate_limit_and_server_errors_are_retried(data):
    sleeps = []
    fail = {"/api/v1/supplier/sales": [429, 502]}
    with fake.FakeWB(data, fail=fail) as srv:
        rows = client(srv, sleeps=sleeps).sales(TODAY)
    assert sleeps[0] == 1.0  # из заголовка X-Ratelimit-Retry
    assert len(sleeps) == 2
    assert isinstance(rows, list)


def test_bad_token_is_a_clear_error(data):
    with fake.FakeWB(data) as srv, pytest.raises(WBError, match="проверьте токен"):
        client(srv, token="wrong").orders(TODAY)


def test_gives_up_after_attempts(data):
    with fake.FakeWB(data, fail={"/api/v1/supplier/orders": [503] * 10}) as srv:
        with pytest.raises(WBError, match="не ответил"):
            client(srv).orders(TODAY)


def test_stocks_task_waits_until_done(data):
    sleeps = []
    with fake.FakeWB(data) as srv:
        stocks = client(srv, sleeps=sleeps).stocks(poll=2)
        assert srv.calls.count("/api/v1/warehouse_remains/tasks/task-1/status") == 2
    assert sleeps == [2]
    assert len(stocks) == len(fake.ITEMS)


def test_ad_costs_split_by_31_days(data):
    with fake.FakeWB(data) as srv:
        rows = client(srv).ad_costs(date(2026, 8, 31), TODAY)  # 45 дней
        assert srv.calls.count("/adv/v1/upd") == 2
    assert len(rows) == sum(1 for a in data["ads"] if a["updTime"] >= "2026-08-31")


def test_orders_wait_for_minute_limit(data):
    sleeps = []
    with fake.FakeWB(data) as srv:
        c = client(srv, sleeps=sleeps)
        c.min_interval = 61
        c.orders(TODAY)
        c.orders(TODAY)
    assert len(sleeps) == 1 and 55 < sleeps[0] <= 61
