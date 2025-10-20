from __future__ import annotations

import json
import types
from typing import Optional

import pytest

from wos_redeem.solver import (
    _extract_captcha_from_content,
    _solve_via_codex_exec,
    solve_captcha_via_openrouter,
)


def test_extract_captcha_from_json_and_text():
    j = json.dumps({"captcha": "Ab12"})
    g, cleaned = _extract_captcha_from_content(j)
    assert g == "Ab12"

    g2, _ = _extract_captcha_from_content("The code is vX9b; answer: vX9b.")
    assert g2 == "vX9b"


def test_codex_exec_timeout(monkeypatch, tmp_path):
    # Cause subprocess.run to timeout
    import subprocess as _sp

    def fake_run(*a, **kw):
        raise _sp.TimeoutExpired(cmd=["codex"], timeout=kw.get("timeout", 0))

    monkeypatch.setattr("wos_redeem.solver.subprocess.run", fake_run)

    # Exercise the function; it should return (None, None) on timeout
    guess, raw = _solve_via_codex_exec(str(tmp_path / "img.jpg"))
    assert guess is None and raw is None


def test_codex_exec_nonzero_returns_combined_stderr_stdout(monkeypatch, tmp_path):
    class R:
        returncode = 2
        stdout = ""
        stderr = "boom"

    def fake_run(*a, **kw):
        return R()

    # Avoid file-read path so it falls back to stdout
    monkeypatch.setattr("wos_redeem.solver.subprocess.run", fake_run)
    g, raw = _solve_via_codex_exec(str(tmp_path / "img.jpg"))
    assert g is None
    assert raw and "boom" in raw


def test_solve_prefers_codex_then_fallback_openrouter(monkeypatch):
    # 1) Codex returns nothing
    monkeypatch.setattr("wos_redeem.solver._solve_via_codex_exec", lambda p: (None, None))

    # 2) OpenRouter returns a content that contains the captcha
    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"captcha": "Z9qK"})}}]}

    monkeypatch.setattr("wos_redeem.solver.requests.post", lambda *a, **k: FakeResp())
    got = solve_captcha_via_openrouter("data:image/jpeg;base64,AA==", api_key="test", max_attempts=1)
    assert got == "Z9qK"


def test_solve_returns_codex_when_valid(monkeypatch):
    monkeypatch.setattr("wos_redeem.solver._solve_via_codex_exec", lambda p: ("A1b2", "A1b2"))
    # OpenRouter should not be called; but even if it is, keep it harmless
    monkeypatch.setattr(
        "wos_redeem.solver.requests.post", lambda *a, **k: pytest.fail("should not hit openrouter")
    )
    got = solve_captcha_via_openrouter("data:image/jpeg;base64,AA==", api_key="ignored", max_attempts=1)
    assert got == "A1b2"

