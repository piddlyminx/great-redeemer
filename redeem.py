#!/usr/bin/env python3
"""
redeem_openrouter.py

End-to-end:
  /player → /captcha(init=0) → solve captcha via OpenRouter (no file saves) → /gift_code

Requirements:
  - Python 3.8+
  - pip install requests
  - Environment variable OPENROUTER_API_KEY set to your key

Usage:
  python redeem_openrouter.py --fid 442818534 --cdk GAECHEONJEOL
  python redeem_openrouter.py --fid 442818534 --cdk GAECHEONJEOL --dump-sign
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from typing import Dict, Optional

import requests
from wos_redeem.utils import save_failure_captcha
from wos_redeem.solver import solve_captcha_via_openrouter as lib_solve, CaptchaSolverError

# ----- API endpoints and constants -----
BASE = "https://wos-giftcode-api.centurygame.com/api"
SECRET = "tB87#kPtkxqOS2"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "qwen/qwen2.5-vl-72b-instruct:free"

CAPTCHA_REGEX = re.compile(r"^[A-Za-z0-9]{4}$")


# ---------- helpers ----------
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
    return hashlib.md5((canonical + SECRET).encode("utf-8")).hexdigest(), canonical


def save_data_url_image(data_url: str, out_path: str) -> str:
    """Decode a data:image/...;base64,... URL and write to out_path.

    Returns the output path. Raises ValueError on unexpected format.
    """
    m = re.match(r"^data:(image/\w+);base64,([A-Za-z0-9+/=]+)$", data_url)
    if not m:
        if "," in data_url:
            b64 = data_url.split(",", 1)[1]
        else:
            raise ValueError("unexpected data URL format (no comma)")
    else:
        b64 = m.group(2)
    img_bytes = base64.b64decode(b64)
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    return out_path


def post_form(path: str, fields: Dict, timeout: int = 60) -> Dict:
    url = BASE + path
    resp = requests.post(url, data=fields, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Non-JSON response from {url}: {resp.text[:400]!r}")


# ---------- API calls ----------
def call_player(fid: int, dump: bool = False, t: Optional[int] = None) -> Dict:
    t = t or now_ms()
    payload = {"fid": fid, "time": t}
    sign, canonical = sign_payload(payload)
    if dump:
        print(f"  /player canonical: {canonical}")
        print(f"  /player sign:      {sign}")
    payload["sign"] = sign
    return post_form("/player", payload)


def call_captcha(fid: int, dump: bool = False, t: Optional[int] = None) -> Dict:
    t = t or now_ms()
    payload = {"fid": fid, "init": 0, "time": t}
    sign, canonical = sign_payload(payload)
    if dump:
        print(f"  /captcha canonical: {canonical}")
        print(f"  /captcha sign:      {sign}")
    payload["sign"] = sign
    return post_form("/captcha", payload)


def call_gift_code(fid: int, cdk: str, captcha_code: str, dump: bool = False, t: Optional[int] = None) -> Dict:
    t = t or now_ms()
    payload = {"fid": fid, "cdk": cdk, "captcha_code": captcha_code, "time": t}
    sign, canonical = sign_payload(payload)
    if dump:
        print(f"  /gift_code canonical: {canonical}")
        print(f"  /gift_code sign:      {sign}")
    payload["sign"] = sign
    return post_form("/gift_code", payload)


# Use shared library solver with explicit guess in exceptions
solve_captcha_via_openrouter = lib_solve


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="CenturyGames gift code redeemer using OpenRouter for captcha OCR")
    ap.add_argument("--fid", type=int, required=True, help="Player fid (numeric)")
    ap.add_argument("--cdk", required=True, help="Gift code text")
    ap.add_argument("--dump-sign", action="store_true", help="Print canonical strings and MD5 signatures")
    ap.add_argument("--verbose", action="store_true", help="Print full JSON responses")
    ap.add_argument("--captcha-only", action="store_true", help="Only do /captcha and exit (for testing)")
    args = ap.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ OPENROUTER_API_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(2)

    fid = args.fid
    cdk = args.cdk

    # Step 1: /player
    print("[1/3] /player ...")
    player = call_player(fid, dump=args.dump_sign)
    if args.verbose:
        print(json.dumps(player, indent=2, ensure_ascii=False))
    if player.get("code") != 0:
        print("❌ /player failed:", player)
        sys.exit(1)
    print("    OK — nickname:", player.get("data", {}).get("nickname"))

    # Step 2: /captcha(init=0)
    print("[2/3] /captcha (init=0) ...")
    cap = call_captcha(fid, dump=args.dump_sign)
    if args.verbose:
        print(json.dumps(cap, indent=2, ensure_ascii=False))
    if cap.get("code") != 0:
        print("❌ /captcha failed:", cap)
        sys.exit(1)

    # The server returns a data URL like "data:image/jpeg;base64,...."
    data_url = cap.get("data", {}).get("img")
    if not isinstance(data_url, str) or "base64," not in data_url:
        print("❌ Unexpected captcha response format (no base64 data URL).", file=sys.stderr)
        sys.exit(1)

    # Step 2b: Solve via OpenRouter (pass the data URL directly; no file writes)
    print("[2b] Solving captcha via OpenRouter (google/gemini-2.0-flash-exp:free) ...")
    try:
        captcha_code = solve_captcha_via_openrouter(data_url, api_key, max_attempts=1)
    except CaptchaSolverError as e:
        print("❌ OpenRouter error:", e, file=sys.stderr)
        # Save the captcha image with the explicit attempted guess from the solver
        try:
            out_path = save_failure_captcha(data_url, fid=fid, guess=(e.guess or "none"), reason="openrouter_error")
            print("  saved failed captcha to:", out_path)
        except Exception as se:
            print("  warning: unable to save failure captcha:", se)
        sys.exit(1)
    except Exception as e:
        print("❌ OpenRouter HTTP/unknown error:", e, file=sys.stderr)
        sys.exit(1)
    print(f"CAPTCHA: {captcha_code}")

    # Step 3: /gift_code
    print("[3/3] /gift_code ...")
    if args.captcha_only:
        print("⚠️  --captcha-only specified; exiting before /gift_code.")
        sys.exit(0)

    redeem = call_gift_code(fid, cdk, captcha_code, dump=args.dump_sign)
    print(json.dumps(redeem, indent=2, ensure_ascii=False))

    if redeem.get("code") == 0:
        print("✅ Redeem success")
    else:
        msg = redeem.get('msg')
        print(f"⚠️  Redeem not successful (code={redeem.get('code')}, err_code={redeem.get('err_code')}): {msg}")
        # Only save when the backend reports CAPTCHA CHECK ERROR
        msg_norm = (str(msg) if isinstance(msg, str) else "").strip().rstrip(".").upper()
        if msg_norm == "CAPTCHA CHECK ERROR":
            try:
                out_path = save_failure_captcha(data_url, fid=fid, guess=(captcha_code or "none"), reason="captcha_check_error")
                print("  saved failed captcha to:", out_path)
            except Exception as e:
                print("  warning: unable to save failure captcha:", e)


if __name__ == "__main__":
    main()
