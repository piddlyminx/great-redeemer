from __future__ import annotations

import base64
import os
import re
import time
from typing import Optional

CAPTCHA_REGEX = re.compile(r"^[A-Za-z0-9]{4}$")


def _data_url_to_bytes(data_url: str) -> tuple[bytes, str]:
    """Decode a data URL to bytes and return (bytes, file_ext).

    Falls back to 'jpg' when type cannot be determined.
    """
    ext = "jpg"
    m = re.match(r"^data:(image/(\w+));base64,([A-Za-z0-9+/=]+)$", data_url)
    if m:
        if m.group(2):
            ext = m.group(2).lower()
        b64 = m.group(3)
    else:
        # best-effort extraction if header is slightly different
        if "," not in data_url:
            raise ValueError("unexpected data URL format (no comma)")
        header, b64 = data_url.split(",", 1)
        m2 = re.search(r"image/(\w+)", header)
        if m2:
            ext = m2.group(1).lower()
    return base64.b64decode(b64), ext


def sanitize_guess(guess: Optional[str]) -> str:
    """Sanitize a guess for use in filenames.

    - Keep alphanumerics; replace others with '-'.
    - Collapse repeats of '-'.
    - Trim to max 24 chars; default to 'unknown' when empty.
    """
    if not guess:
        return "unknown"
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(guess)).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:24] or "unknown"


def extract_guess_from_text(text: str) -> str:
    """Try to extract a 4-char alphanumeric guess from arbitrary text.

    Falls back to a sanitized snippet of the text when no exact match is found.
    """
    m = re.search(r"[A-Za-z0-9]{4}", text)
    if m:
        return m.group(0)
    # grab a short snippet for context
    snippet = text.strip().splitlines()[0][:20]
    return sanitize_guess(snippet) or "unknown"


def save_failure_captcha(data_url: str, fid: int, guess: Optional[str] = None, reason: Optional[str] = None) -> str:
    """Save the CAPTCHA image in ./failures with timestamp and guess in filename.

    Returns the output path.
    """
    os.makedirs("failures", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    img_bytes, ext = _data_url_to_bytes(data_url)
    guess_part = sanitize_guess(guess)
    reason_part = f"_{sanitize_guess(reason)}" if reason else ""
    filename = f"captcha_{ts}_fid{fid}_{guess_part}{reason_part}.{ext}"
    out_path = os.path.join("failures", filename)
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    return out_path

