import httpx
import unittest

from app.leads.gateway_client import GatewayError, GoogleSheetsGatewayClient
from app.settings import GoogleSheetsSettings


def settings():
    return GoogleSheetsSettings("https://script.google.com/macros/s/test/exec", "x" * 32, 0, 5, 100, 30, False)


def client(response):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    return GoogleSheetsGatewayClient(settings(), client=httpx.Client(transport=transport))


def test_get_tail(self):
    with client({"ok": True, "last_row": 26, "next_row": 27, "gid": 0, "sheet_name": "工作表1"}) as api:
        assert api.get_tail().next_row == 27


def test_read_rows():
    payload = {"ok": True, "headers": ["id"], "values": [["l:1"]], "start_row": 27, "next_row": 28, "last_row": 27, "has_more": False}
    with client(payload) as api:
        result = api.read_rows(27)
        assert result.values == [["l:1"]]
        assert result.has_more is False


def test_gateway_error_and_input_validation():
    with client({"ok": False, "error": "unauthorized"}) as api:
        with pytest.raises(GatewayError, match="认证失败"):
            api.get_tail()
    with client({"ok": True}) as api:
        with pytest.raises(ValueError):
            api.read_rows(1)

