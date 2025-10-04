import importlib
import os
import types
import hashlib


def _reload_api_with_secret(secret: str):
    os.environ["WOS_SECRET"] = secret
    # Lazy import to ensure SECRET is read from env at import-time
    import wos_redeem.api as api  # type: ignore

    importlib.reload(api)
    return api


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 0, "msg": "SUCCESS", "echo": self._payload}


def test_sign_payload_no_captcha(monkeypatch):
    api = _reload_api_with_secret("TESTSECRET")

    payload = {"fid": 123, "cdk": "ABC", "time": 1700000000000}
    # Expected canonical: keys sorted -> cdk,fid,time
    canonical = "cdk=ABC&fid=123&time=1700000000000"
    expected = hashlib.md5((canonical + "TESTSECRET").encode("utf-8")).hexdigest()

    got = api.sign_payload(payload)
    assert got == expected


def test_sign_payload_with_captcha_included(monkeypatch):
    api = _reload_api_with_secret("X")

    # Ensure sign changes when captcha_code is present in canonical string
    p1 = {"fid": 1, "cdk": "AAA", "time": 1}
    p2 = {"fid": 1, "cdk": "AAA", "captcha_code": "ZZZZ", "time": 1}
    s1 = api.sign_payload(p1)
    s2 = api.sign_payload(p2)
    assert s1 != s2


def test_call_gift_code_with_captcha(monkeypatch):
    api = _reload_api_with_secret("X")

    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = dict(data)
        return DummyResponse(captured["data"])

    monkeypatch.setattr("wos_redeem.api.requests.post", fake_post)

    fixed_t = 1700000000000
    resp = api.call_gift_code(77, "WORLD", "A1b2", t=fixed_t)
    assert resp["code"] == 0
    sent = captured["data"]
    assert sent["captcha_code"] == "A1b2"
    assert isinstance(sent.get("sign"), str)
