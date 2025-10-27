from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from wos_redeem.captcha_solver import GiftCaptchaSolver, ONNX_AVAILABLE


CAPTCHA_RE = re.compile(r"^[A-Za-z0-9]{4}$")


@pytest.mark.skipif(not ONNX_AVAILABLE, reason="onnxruntime not available")
def test_onnx_solver_initializes():
    solver = GiftCaptchaSolver()
    assert solver.is_initialized, "ONNX solver failed to initialize (model files missing?)"


@pytest.mark.skipif(not ONNX_AVAILABLE, reason="onnxruntime not available")
def test_onnx_solver_returns_4_char_codes_on_sample_failures():
    # Take a small, deterministic sample to keep test time reasonable
    failures_dir = Path("failures")
    if not failures_dir.exists():
        pytest.skip("failures/ folder not present")

    sample_images = sorted([p for p in failures_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}])[:5]
    if not sample_images:
        pytest.skip("no images in failures/")

    solver = GiftCaptchaSolver()
    if not solver.is_initialized:
        pytest.skip("ONNX solver not initialized (missing model files)")

    for img_path in sample_images:
        img_bytes = img_path.read_bytes()
        guess, ok, method, conf, _ = solver.solve_captcha(img_bytes, fid=None, attempt=0)
        assert ok, f"solver did not return success for {img_path.name}"
        assert isinstance(guess, str) and CAPTCHA_RE.fullmatch(guess), f"invalid guess '{guess}' from {img_path.name}"
