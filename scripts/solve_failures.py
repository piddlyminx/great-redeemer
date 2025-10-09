#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path
from typing import Iterable

from wos_redeem.solver import solve_captcha_via_openrouter, CaptchaSolverError


MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def file_to_data_url(path: Path) -> str:
    ext = path.suffix.lower()
    mime = MIME_BY_EXT.get(ext, "image/jpeg")
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def iter_images(root: Path) -> Iterable[Path]:
    for p in sorted(root.glob("*")):
        if p.is_file() and p.suffix.lower() in MIME_BY_EXT:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="Attempt solving CAPTCHA images in ./failures via OpenRouter")
    ap.add_argument("--dir", default="failures", help="Directory containing saved CAPTCHA images (default: failures)")
    ap.add_argument("--limit", type=int, default=0, help="Max images to attempt (0 = all)")
    ap.add_argument("--shuffle", action="store_true", help="Shuffle order before attempting")
    args = ap.parse_args()

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY not set; export it and re-run", file=sys.stderr)
        return 2

    root = Path(args.dir)
    if not root.exists():
        print(f"Directory not found: {root}", file=sys.stderr)
        return 2

    files = list(iter_images(root))
    if args.shuffle:
        import random

        random.shuffle(files)
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    if not files:
        print("No image files found in", root)
        return 0

    ok = 0
    fail = 0
    for i, path in enumerate(files, 1):
        try:
            data_url = file_to_data_url(path)
            guess, conf = solve_captcha_via_openrouter(data_url, api_key, return_confidence=True)  # type: ignore[assignment]
            ok += 1
            conf_s = f" {conf:.2f}" if isinstance(conf, float) else ""
            print(f"[{i}/{len(files)}] OK   {path.name:60s} -> {guess}{conf_s}")
        except CaptchaSolverError as e:
            fail += 1
            g = e.guess or "-"
            print(f"[{i}/{len(files)}] FAIL {path.name:60s} -> {g}  ({e})")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(files)}] ERR  {path.name:60s} -> {e}")

    print(f"Done. Success={ok} Fail={fail} Total={len(files)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
