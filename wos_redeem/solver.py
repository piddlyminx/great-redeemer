from __future__ import annotations

import json
import os
import re
import logging
import time
from typing import Optional, Tuple
import subprocess
import base64
from urllib.parse import urlparse

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen2.5-vl-72b-instruct:free")
# Secondary and tiebreaker models
VISION_MODEL_PRIMARY = os.getenv("OPENROUTER_MODEL_PRIMARY", OPENROUTER_MODEL)
TIEBREAKER_MODEL = os.getenv("OPENROUTER_MODEL_TIEBREAKER", "mistralai/mistral-small-3.2-24b-instruct:free")
THROTTLE_BETWEEN_CALLS_S = float(os.getenv("OPENROUTER_THROTTLE_S", "2"))
THROTTLE_ON_429_S = float(os.getenv("OPENROUTER_429_SLEEP_S", "5"))
CAPTCHA_REGEX = re.compile(r"^[A-Za-z0-9]{4}$")
# Solver selection: "codex", "openrouter", or "auto" (default: auto)
CAPTCHA_SOLVER = os.getenv("CAPTCHA_SOLVER", "auto").strip().lower()


class CaptchaSolverError(Exception):
    """Raised when the model returned an invalid/absent captcha string.

    Carries the best-available guess and raw content for logging/filenames.
    """

    def __init__(self, message: str, guess: Optional[str] = None, content: Optional[str] = None):
        super().__init__(message)
        self.guess = guess
        self.content = content


_LOGGER = logging.getLogger("captcha_solver")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.addHandler(logging.StreamHandler())


def _extract_captcha_from_content(content: str) -> Tuple[Optional[str], str]:
    """Return (captcha, cleaned_content) from a model content string.

    Accepts either a JSON object {"captcha":"AB12"} or any 4-char alnum in the text.
    """
    content_clean = content.strip()
    if content_clean.startswith("```"):
        m = re.search(r"\{.*\}", content_clean, flags=re.DOTALL)
        if m:
            content_clean = m.group(0)
    captcha: Optional[str] = None
    try:
        obj = json.loads(content_clean)
        if isinstance(obj, dict) and "captcha" in obj:
            captcha = str(obj["captcha"]).strip()
    except json.JSONDecodeError:
        m = re.search(CAPTCHA_REGEX, content_clean)
        if m:
            captcha = m.group(0)
    return (captcha if (captcha and CAPTCHA_REGEX.fullmatch(captcha)) else None, content_clean)


def _call_openrouter(api_key: str, model: str, messages: list[dict], max_attempts: int = 2, max_tokens: int = 20) -> Tuple[Optional[str], Optional[str]]:
    """Call a specific OpenRouter model and parse a captcha guess.

    Returns (guess, cleaned_content) where either item may be None on failure.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    last_content: Optional[str] = None
    for attempt_idx in range(max_attempts):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
            # Handle rate limit explicitly
            if getattr(resp, "status_code", None) == 429:
                time.sleep(THROTTLE_ON_429_S)
                # proceed to next loop iteration (try again)
                # If this was the last attempt, the outer sleep-after-attempt still happens below
                # to keep consistent spacing between calls
                if attempt_idx < max_attempts - 1 and THROTTLE_BETWEEN_CALLS_S > 0:
                    time.sleep(THROTTLE_BETWEEN_CALLS_S)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            _LOGGER.info(f"[solver] openrouter http error model={model}: {e}")
            # If requests raised an HTTPError, check for 429
            try:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 429:
                    time.sleep(THROTTLE_ON_429_S)
            except Exception:
                pass
            # Short spacing before next retry attempt
            if attempt_idx < max_attempts - 1 and THROTTLE_BETWEEN_CALLS_S > 0:
                time.sleep(THROTTLE_BETWEEN_CALLS_S)
            continue
        try:
            content = data["choices"][0]["message"]["content"]
            guess, cleaned = _extract_captcha_from_content(content)
            last_content = cleaned
            if guess:
                return guess, last_content
        except Exception as e:
            _LOGGER.info(f"[solver] openrouter parse error model={model}: {e}")
            # spacing before next retry
            if attempt_idx < max_attempts - 1 and THROTTLE_BETWEEN_CALLS_S > 0:
                time.sleep(THROTTLE_BETWEEN_CALLS_S)
            continue
        # If no return (no guess), space before next attempt
        if attempt_idx < max_attempts - 1 and THROTTLE_BETWEEN_CALLS_S > 0:
            time.sleep(THROTTLE_BETWEEN_CALLS_S)
    return None, last_content


def solve_captcha_via_openrouter(data_url: str, api_key: str, max_attempts: int = 2) -> str:
    system_prompt = (
        "You are a vision assistant that reads short CAPTCHA images.\n"
        "Rules:\n"
        " - The CAPTCHA is exactly 4 case-sensitive alphanumeric characters [A-Za-z0-9].\n"
        " - Do not include spaces or quotes.\n"
        " - Respond ONLY with a compact JSON object of the form: {\"captcha\":\"AB12\"}.\n"
        " - If uncertain, return your best guess in the same JSON format."
    )

    vision_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Extract the 4-character CAPTCHA from this image."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    # Decide strategy based on CAPTCHA_SOLVER
    use_codex = CAPTCHA_SOLVER in ("auto", "codex")
    use_openrouter = CAPTCHA_SOLVER in ("auto", "openrouter")

    last_c_raw = None
    last_q_raw = None

    if use_codex:
        img_path = _write_temp_image(data_url)
        c_guess, c_raw = _solve_via_codex_exec(img_path)
        last_c_raw = c_raw
        _LOGGER.info(json.dumps({"event": "captcha_guess", "model": "codex/exec", "guess": c_guess, "content": (c_raw or "")[:200]}))
        if c_guess and CAPTCHA_REGEX.fullmatch(c_guess):
            return c_guess

    if use_openrouter:
        if THROTTLE_BETWEEN_CALLS_S > 0 and use_codex:
            time.sleep(THROTTLE_BETWEEN_CALLS_S)
        q_guess, q_raw = _call_openrouter(api_key, VISION_MODEL_PRIMARY, vision_messages, max_attempts=max_attempts)
        last_q_raw = q_raw
        _LOGGER.info(json.dumps({"event": "captcha_guess", "model": VISION_MODEL_PRIMARY, "guess": q_guess, "content": (q_raw or "")[:200]}))
        if q_guess and CAPTCHA_REGEX.fullmatch(q_guess):
            return q_guess

    # Neither produced a valid code
    combined = "_".join([(last_c_raw or "none"), (last_q_raw or "none")])
    raise CaptchaSolverError(
        "no valid captcha from selected solvers",
        guess=combined,
        content=json.dumps({"codex_raw": last_c_raw, "qwen_raw": last_q_raw})[:500],
    )


def _write_temp_image(data_url: str, path: str = "./temp.jpg") -> str:
    """Save the captcha image to a local path usable by external tools (codex exec).

    Supports http(s) URLs and data: URLs. Returns the written path.
    """
    try:
        if data_url.startswith("data:"):
            # data:[<mediatype>][;base64],<data>
            header, b64data = data_url.split(",", 1)
            if ";base64" in header:
                raw = base64.b64decode(b64data)
            else:
                raw = b64data.encode("utf-8", errors="ignore")
            with open(path, "wb") as f:
                f.write(raw)
            return path
        # Otherwise, fetch via HTTP(S)
        u = urlparse(data_url)
        if u.scheme in {"http", "https"}:
            r = requests.get(data_url, timeout=60)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return path
        # Fallback: assume it's a local file path already
        return data_url
    except Exception as e:
        _LOGGER.info(f"[solver] failed writing temp image: {e}")
        # Still return a path so caller can attempt; but more likely qwen fallback will be used
        return path


def _solve_via_codex_exec(image_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Invoke `codex exec` to read the 4-char captcha from image_path.

    Returns (guess, raw_text). Guess is None on failure.
    """
    prompt = f"Read the 4 alphanumeric characters (case sensitive) in {image_path}"
    raw: Optional[str] = None
    try:
        import tempfile, os
        # Prefer writing last message to a temp file (more reliable than stdout banners)
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            out_path = tf.name
        codex_bin = os.getenv("CODEX_BIN", "codex")
        extra = os.getenv("CODEX_EXEC_OPTS", "").strip()
        cmd = [codex_bin, "exec", "-c", "model_reasoning_effort=low", "--output-last-message", out_path]
        if extra:
            # naive split; keep simple flags space-separated
            cmd += extra.split()
        cmd.append(prompt)
        res = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            _LOGGER.info(
                json.dumps({
                    "event": "codex_exec_error",
                    "returncode": res.returncode,
                    "stderr": (res.stderr or "").strip()[:400],
                    "stdout": (res.stdout or "").strip()[:200],
                })
            )
            # surface some content for upstream logs
            combined = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
            return None, combined[:500]
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except Exception:
            raw = None
        finally:
            try:
                os.remove(out_path)
            except Exception:
                pass
        # Fallback to stdout if file empty
        if not raw:
            raw = (res.stdout or "").strip()
    except subprocess.TimeoutExpired as e:
        _LOGGER.info(
            json.dumps({
                "event": "codex_exec_timeout",
                "timeout_s": 15,
                "stderr": (getattr(e, "stderr", "") or "")[:200],
                "stdout": (getattr(e, "output", "") or "")[:200],
            })
        )
        return None, None
    except Exception as e:
        _LOGGER.info(f"[solver] codex exec failed: {e}")
        return None, None
    if not raw:
        return None, None
    guess, cleaned = _extract_captcha_from_content(raw)
    return guess, (cleaned or raw)
