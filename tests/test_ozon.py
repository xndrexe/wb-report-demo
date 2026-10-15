"""Ozon: клиент против местного сервера с форматом Seller API, разбор ответов, запуск целиком."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from openpyxl import load_workbook

from wbreport import __main__ as app
from wbreport import config, ozon_api, ozon_fake, ozon_report
from wbreport.ozon_api import OzonClient, OzonError

TODAY = date(2026, 10, 14)
SINCE = datetime(2026, 8, 25, tzinfo=UTC)
TO = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.fixture
def data():
    return ozon_fake.generate(TODAY, days=45)


def client(srv, key=ozon_fake.API_KEY, sleeps=None):
    return OzonClient(
        ozon_fake.CLIENT_ID,
        key,
        url=srv.url,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


# --- клиент ------------------------------------------------------------------


def test_fbo_pages_by_offset_inside_weeks(data, monkeypatch):
    monkeypatch.setattr(ozon_api, "FBO_PAGE", 50)
    with ozon_fake.FakeOzon(data) as srv:
        rows = client(srv).fbo_postings(SINCE, TO)
        calls = [b for p, b in srv.calls if p == "/v2/posting/fbo/list"]
    assert {r["posting_number"] for r in rows} == {p["posting_number"] for p in data["fbo"]}
    assert len(rows) == len(data["fbo"])  # стык недель не дал дублей
    assert all(r["delivery_schema"] == "FBO" for r in rows)
    assert {c["filter"]["since"] for c in calls} >= {"2026-08-25T00:00:00Z", "2026-09-01T00:00:00Z"}
    assert max(c["offset"] for c in calls) > 0


def test_fbs_v4_pages_by_cursor_100_max(data):
    with ozon_fake.FakeOzon(data) as srv:
        rows = client(srv).fbs_postings(SINCE, TO)
        calls = [b for p, b in srv.calls if p == "/v4/posting/fbs/list"]
    assert len(data["fbs"]) > 100 and len(calls) >= 3
    assert len(rows) == len(data["fbs"]) and all(r["delivery_schema"] == "FBS" for r in rows)
    assert "cursor" not in calls[0] and calls[1]["cursor"] == "100"


def test_products_stocks_by_sku_batches_and_returns_by_last_id(data, monkeypatch):
    monkeypatch.setattr(ozon_api, "SKU_BATCH", 3)
    monkeypatch.setattr(ozon_api, "RETURNS_PAGE", 10)
    with ozon_fake.FakeOzon(data) as srv:
        c = client(srv)
        products = c.products()
        skus = [s["sku"] for p in products for s in p["stocks"][:1]]
        fbo = c.fbo_stocks(skus)
        rets = c.returns(SINCE, TO)
        stock_calls = [b for p, b in srv.calls if p == "/v1/analytics/stocks"]
    assert len(products) == len(ozon_fake.ITEMS)
    assert len(stock_calls) == 3 and all(len(b["skus"]) <= 3 for b in stock_calls)
    assert all(isinstance(s, str) for b in stock_calls for s in b["skus"])  # SKU в запросе — строками
    assert len(fbo) == len(data["stocks"])
    assert len(rets) == len(data["returns"]) and len({r["id"] for r in rets}) == len(rets)


def test_accruals_for_a_day_page_by_last_id(data, monkeypatch):
    monkeypatch.setitem(ozon_fake.FakeOzon.PAGE, "/v1/finance/accrual/by-day", 10)
    day = date(2026, 10, 1)
    expected = [a for a in data["accruals"] if a["date"] == day.isoformat()]
    with ozon_fake.FakeOzon(data) as srv:
        rows = client(srv).accruals(day)
        calls = [b for p, b in srv.calls if p == "/v1/finance/accrual/by-day"]
    assert len(expected) > ozon_fake.FakeOzon.PAGE["/v1/finance/accrual/by-day"]
    assert [a["accrual_id"] for a in rows] == [a["accrual_id"] for a in expected]
    assert calls[0] == {"date": "2026-10-01", "last_id": ""} and calls[1]["last_id"]


def test_rate_limit_and_server_errors_are_retried(data):
    sleeps = []
    with ozon_fake.FakeOzon(data, fail={"/v1/returns/list": [429, 503]}) as srv:
        rows = client(srv, sleeps=sleeps).returns(SINCE, TO)
    assert sleeps == [1.0, 10]  # из Retry-After, затем своя пауза
    assert rows


def test_bad_key_is_a_clear_error(data):
    with ozon_fake.FakeOzon(data) as srv, pytest.raises(OzonError, match="Client-Id и API-ключ"):
        client(srv, key="wrong").products()


def test_server_gives_up_after_attempts(data):
    with ozon_fake.FakeOzon(data, fail={"/v4/product/info/stocks": [500] * 4}) as srv:
        with pytest.raises(OzonError, match="не ответил"):
            client(srv).products()


# --- разбор ------------------------------------------------------------------

D = date(2026, 10, 14)


def posting(ts, offer="A", price="1000.0000", qty=1, status="delivered", schema="FBO"):
    item = {"offer_id": offer, "sku": 1, "name": "Плед", "quantity": qty}
    item["price"] = price if schema == "FBO" else {"amount": price, "currency": "RUB"}
    return {"in_process_at": ts, "status": status, "delivery_schema": schema, "products": [item]}


def ret(ts, kind="ClientReturn", offer="A", qty=1, why="Не подошёл размер"):
    return {
        "type": kind,
        "return_reason_name": why,
        "product": {"offer_id": offer, "sku": 1, "name": "Плед", "quantity": qty},
        "logistic": {"return_date": ts},
    }


def acc(day, amount, cat="POSTING"):
    return {"date": day, "accrued_category": cat, "total_amount": {"amount": amount, "currency": "RUB"}}


def test_msk_day_crosses_midnight():
    assert ozon_report.msk_day("2026-10-13T20:59:59Z") == "2026-10-13"
    assert ozon_report.msk_day("2026-10-13T21:00:00Z") == "2026-10-14"
    assert ozon_report.msk_day("2026-10-13T21:00:00.123456Z") == "2026-10-14"
    assert ozon_report.msk_day("") == ""


def test_sales_by_day_fbo_fbs_cancels_returns_and_accruals():
    postings = [
        posting("2026-10-14T07:00:00Z", price="1000.0000", qty=2),
        posting("2026-10-13T22:00:00Z", price="500.00", schema="FBS"),  # 01:00 МСК 14.10
        posting("2026-10-14T08:00:00Z", status="cancelled", schema="FBS", price="300.00"),
    ]
    returns = [
        ret("2026-10-14T10:00:00Z", qty=2),
        ret("2026-10-14T11:00:00Z", kind="Cancellation"),  # отмена — уже в «Отмены», не возврат
    ]
    accruals = [
        acc("2026-10-14", "780.50"),
        acc("2026-10-14", "-20.00", "ITEM"),
        acc("2026-10-14", "-100", "NON_ITEM"),
    ]
    sh = ozon_report.sales_sheet(postings, returns, accruals, D, D)
    assert sh.rows[0] == ["14.10.2026", 4, 2, 2, 2800, 1, 2, 780.5, -120, 660.5]
    assert sh.total[0] == "Итого" and sh.total[1:] == sh.rows[0][1:]
    assert sh.money_cols == {4, 7, 8, 9}


def test_sales_rows_cover_every_day_newest_first():
    sh = ozon_report.sales_sheet([], [], [], date(2026, 10, 12), D)
    assert [r[0] for r in sh.rows] == ["14.10.2026", "13.10.2026", "12.10.2026"]
    assert sh.total == ["Итого", 0, 0, 0, 0, 0, 0, 0, 0, 0]


def test_in_period_cuts_by_moscow_day():
    early = posting("2026-10-13T20:00:00Z")  # 23:00 МСК 13.10 — до периода
    late = posting("2026-10-14T20:59:00Z")  # 23:59 МСК 14.10 — внутри
    p, r, a = ozon_report.in_period([early, late], [], [acc("2026-10-15", "1")], D, D)
    assert p == [late] and r == [] and a == []


def test_stocks_days_left_from_ozon_and_own_warehouse():
    products = [
        {
            "offer_id": "A",
            "stocks": [
                {"type": "fbo", "sku": 7, "present": 99, "reserved": 0},  # FBO берём из аналитики
                {"type": "fbs", "sku": 7, "present": 6, "reserved": 2},
                {"type": "rfbs", "sku": 7, "present": 1, "reserved": 3},  # резерв больше — не минус
            ],
        },
        {"offer_id": "B", "stocks": []},
    ]
    fbo = [
        {"sku": 7, "offer_id": "A", "name": "Плед", "cluster_name": "Казань", "available_stock_count": 4},
        {
            "sku": 7,
            "cluster_name": "Москва",
            "available_stock_count": 2,
            "transit_stock_count": 10,
            "return_from_customer_stock_count": 1,
        },
    ]
    postings = [posting("2026-10-14T07:00:00Z", qty=3), posting("2026-10-14T07:00:00Z", status="cancelled")]
    sh = ozon_report.stocks_sheet(products, fbo, postings, days=3)
    assert sh.headers[3:5] == ["Казань", "Москва"]
    assert sh.rows[0] == ["A", 7, "Плед", 4, 2, 6, 4, 10, 1, 1.0, 10]
    assert sh.rows[1] == ["B", "", "", 0, 0, 0, 0, 0, 0, 0.0, ""]  # нет заказов — нет прогноза
    assert sh.warn == (len(sh.headers) - 1, 14)


def test_items_sheet_returns_share_and_top_reason():
    postings = [posting("2026-10-14T07:00:00Z", qty=1) for _ in range(5)]
    postings.append(posting("2026-10-14T07:00:00Z", status="cancelled"))
    postings.append(posting("2026-10-14T07:00:00Z", offer="B", price="5000.0000"))
    returns = [ret("t"), ret("t", why="Брак"), ret("t", why="Брак"), ret("t", kind="Cancellation", why="x")]
    sh = ozon_report.items_sheet(postings, returns)
    assert sh.rows[0][:4] == ["A", 1, "Плед", 6]  # больше выручка — выше
    assert sh.rows[0][4:] == [6000, 1, 3, 60.0, "Брак"]
    assert sh.rows[1][0] == "B" and sh.rows[1][7] == 0.0
    assert sh.total == ["Итого", "", "", 7, 11000, 1, 3, 50.0, ""]


def test_items_sheet_product_only_in_returns():
    sh = ozon_report.items_sheet([], [ret("t", offer="Z")])
    assert sh.rows == [["Z", 1, "Плед", 0, 0, 0, 1, "", "Не подошёл размер"]]


# --- запуск целиком ----------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return replace(config.load(), market="ozon", days=30, excel_path=tmp_path / "oz.xlsx", gsheet_id="")


def test_run_ozon_matches_raw_data(cfg, data):
    with ozon_fake.FakeOzon(data) as srv:
        sheets = app.run_ozon(cfg, client(srv), today=TODAY)
        fbo_call = next(b for p, b in srv.calls if p == "/v2/posting/fbo/list")
        acc_days = [b["date"] for p, b in srv.calls if p == "/v1/finance/accrual/by-day" and not b["last_id"]]
    assert fbo_call["filter"]["since"] == "2026-09-14T21:00:00Z"  # 00:00 МСК 15.09
    assert acc_days[0] == "2026-09-15" and acc_days[-1] == "2026-10-14" and len(acc_days) == 30

    wb = load_workbook(cfg.excel_path)
    assert wb.sheetnames == ["Продажи и заказы", "Остатки", "Заказы и возвраты по товарам"]
    assert wb["Продажи и заказы"]["A1"].fill.fgColor.rgb.endswith("005BFF")  # цвета Ozon

    lo, hi = "2026-09-15", "2026-10-14"
    units = sum(
        i["quantity"]
        for p in data["fbo"] + data["fbs"]
        if lo <= ozon_report.msk_day(p["in_process_at"]) <= hi
        for i in p["products"]
    )
    days = list(wb["Продажи и заказы"].values)
    assert len(days) == 1 + 30 + 1 and days[-1][0] == "Итого" and days[-1][1] == units
    assert days[-1][2] + days[-1][3] == units
    assert sheets[2].total[3] == units  # лист по товарам сходится с листом по дням

    stocks = list(wb["Остатки"].values)
    assert len(stocks) == 1 + len(ozon_fake.ITEMS)
    pillow = next(r for r in stocks if r[0] == "PILLOW-50x70")
    assert pillow[-1] < 14
    cell = wb["Остатки"].cell(row=stocks.index(pillow) + 1, column=len(stocks[0]))
    assert cell.fill.fgColor.rgb.endswith("F8D7DA")


def test_demo_mode_ozon_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCEL_PATH", str(tmp_path / "demo.xlsx"))
    app.main(["--market", "ozon", "--demo", "--days", "7"])
    wb = load_workbook(tmp_path / "demo.xlsx")
    assert len(list(wb["Продажи и заказы"].values)) == 9
    assert "Заказы и возвраты по товарам" in wb.sheetnames


def test_ozon_without_key_stops_with_hint(monkeypatch):
    empty = replace(config.load(), market="ozon", ozon_client_id="", ozon_api_key="")
    monkeypatch.setattr(app.config, "load", lambda: empty)
    with pytest.raises(SystemExit, match="OZON_API_KEY"):
        app.main([])


def test_market_from_env_and_own_excel(monkeypatch):
    monkeypatch.delenv("EXCEL_PATH", raising=False)
    monkeypatch.setenv("MARKETPLACE", "Ozon")
    cfg = config.load()
    assert cfg.market == "ozon" and cfg.excel_path == config.ROOT / "out" / "ozon_report.xlsx"
    monkeypatch.setenv("MARKETPLACE", "ym")
    with pytest.raises(SystemExit, match="wb или ozon"):
        config.load()


def test_market_flag_overrides_env(monkeypatch, tmp_path):
    monkeypatch.delenv("EXCEL_PATH", raising=False)
    monkeypatch.setenv("MARKETPLACE", "wb")
    seen = {}
    monkeypatch.setattr(app, "run_ozon", lambda cfg, c: seen.setdefault("cfg", cfg))
    monkeypatch.setenv("OZON_CLIENT_ID", "1")
    monkeypatch.setenv("OZON_API_KEY", "k")
    app.main(["--market", "ozon"])
    assert seen["cfg"].market == "ozon" and seen["cfg"].excel_path.name == "ozon_report.xlsx"
