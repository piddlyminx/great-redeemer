from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, Optional

import requests

BASE = "https://wos-giftcode-api.centurygame.com/api"
SECRET = os.getenv("WOS_SECRET", "tB87#kPtkxqOS2")
API_REFERER = os.getenv("WOS_API_REFERER", "https://wos-giftcode.centurygame.com/")
API_ORIGIN = os.getenv("WOS_API_ORIGIN", "https://wos-giftcode.centurygame.com")
API_USER_AGENT = os.getenv(
    "WOS_API_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)


def now_ms() -> int:
    return int(time.time() * 1000)


def canonicalize(obj: Dict) -> str:
    parts = []
    for k in sorted(obj.keys()):
        v = obj[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        else:
            v = str(v)
        parts.append(f"{k}={v}")
    return "&".join(parts)


def sign_payload(payload: Dict) -> str:
    canonical = canonicalize(payload)
    return hashlib.md5((canonical + SECRET).encode("utf-8")).hexdigest()


def post_form(path: str, fields: Dict, timeout: int = 60) -> Dict:
    url = BASE + path
    headers = {
        "Referer": API_REFERER,
        "Origin": API_ORIGIN,
        "User-Agent": API_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.post(url, data=fields, headers=headers, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response from {url}: {resp.text[:400]!r}")


def call_player(fid: int, t: Optional[int] = None) -> Dict:
    t = t or now_ms()
    payload = {"fid": fid, "time": t}
    payload["sign"] = sign_payload(payload)
    return post_form("/player", payload)


def call_captcha(fid: int, t: Optional[int] = None) -> Dict:
    t = t or now_ms()
    payload = {"fid": fid, "init": 0, "time": t}
    payload["sign"] = sign_payload(payload)
    return post_form("/captcha", payload)


def call_gift_code(
    fid: int,
    cdk: str,
    captcha_code: str,
    t: Optional[int] = None,
) -> Dict:
    """Call /gift_code endpoint (captcha required).

    As of 2025-10-04, backend requires a valid ``captcha_code`` for
    redemption. This call always includes ``captcha_code`` in the signed
    payload and does not attempt a no-captcha redeem.
    """
    t = t or now_ms()
    payload: Dict[str, object] = {
        "fid": fid,
        "cdk": cdk,
        "captcha_code": captcha_code,
        "time": t,
    }
    payload["sign"] = sign_payload(payload)
    return post_form("/gift_code", payload)
