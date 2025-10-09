from __future__ import annotations

import json
import os
import re
from typing import Optional, Tuple, Union

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen2.5-vl-72b-instruct:free")
CAPTCHA_REGEX = re.compile(r"^[A-Za-z0-9]{4}$")


class CaptchaSolverError(Exception):
    """Raised when the model returned an invalid/absent captcha string.

    Carries the best-available guess and raw content for logging/filenames.
    """

    def __init__(self, message: str, guess: Optional[str] = None, content: Optional[str] = None):
        super().__init__(message)
        self.guess = guess
        self.content = content


def solve_captcha_via_openrouter(
    data_url: str,
    api_key: str,
    max_attempts: int = 2,
    *,
    return_confidence: bool = False,
) -> Union[str, Tuple[str, Optional[float]]]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a vision assistant that reads short CAPTCHA images.\n"
        "Rules:\n"
        " - The CAPTCHA is exactly 4 case-sensitive alphanumeric characters [A-Za-z0-9].\n"
        " - Output MUST be a single compact JSON object: {\"captcha\":\"AB12\",\"confidence\":0.82}.\n"
        " - confidence is a number in [0,1] indicating how sure you are the captcha is correct.\n"
        " - Always include both keys. No extra fields, no prose, no backticks.\n"
        " - If uncertain, still provide your best 4-character guess and reflect that in confidence."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract the 4-character CAPTCHA from this image."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "max_tokens": 20,
    }

    last_err: Optional[str] = None
    last_guess: Optional[str] = None
    last_conf: Optional[float] = None
    last_content: Optional[str] = None
    for _ in range(max_attempts):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            last_err = f"OpenRouter HTTP error: {e}"
            continue

        try:
            content = data["choices"][0]["message"]["content"]
            content_clean = content.strip()
            if content_clean.startswith("```"):
                m = re.search(r"\{.*\}", content_clean, flags=re.DOTALL)
                if m:
                    content_clean = m.group(0)

            captcha = None
            conf: Optional[float] = None
            try:
                obj = json.loads(content_clean)
                if isinstance(obj, dict):
                    # Preferred schema: {"captcha": "AB12", "confidence": 0.82}
                    if "captcha" in obj:
                        captcha = str(obj["captcha"]).strip()
                    if "confidence" in obj:
                        try:
                            conf_val = float(obj["confidence"])  # type: ignore[arg-type]
                            # clamp to [0,1]
                            if conf_val < 0:
                                conf_val = 0.0
                            if conf_val > 1:
                                conf_val = 1.0
                            conf = conf_val
                        except Exception:
                            conf = None
                    last_guess = captcha if captcha else last_guess
                    last_conf = conf if conf is not None else last_conf
            except json.JSONDecodeError:
                # Fallback: extract 4-char alnum anywhere in the string
                m = re.search(CAPTCHA_REGEX, content_clean)
                if m:
                    captcha = m.group(0)
                    last_guess = captcha
                    conf = None
            last_content = content_clean

            if not captcha or not CAPTCHA_REGEX.fullmatch(captcha):
                last_err = f"Invalid captcha format from model: {content_clean[:200]!r}"
                continue
            if return_confidence:
                return captcha, conf
            return captcha
        except Exception as e:
            last_err = f"OpenRouter parse error: {e}"
            continue

    # Exhausted attempts without a valid captcha
    raise CaptchaSolverError(last_err or "OpenRouter failed to produce a valid captcha", guess=last_guess, content=last_content)
