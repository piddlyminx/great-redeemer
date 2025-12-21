#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from wos_redeem.captcha_solver import GiftCaptchaSolver, ONNX_AVAILABLE


MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def iter_images(root: Path) -> Iterable[Path]:
    for p in sorted(root.glob("*")):
        if p.is_file() and p.suffix.lower() in MIME_BY_EXT:
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="Attempt solving CAPTCHA images in ./failures via local ONNX solver")
    ap.add_argument("--dir", default="failures", help="Directory containing saved CAPTCHA images (default: failures)")
    ap.add_argument("--limit", type=int, default=0, help="Max images to attempt (0 = all)")
    ap.add_argument("--shuffle", action="store_true", help="Shuffle order before attempting")
    args = ap.parse_args()

    if not ONNX_AVAILABLE:
        print("ONNX runtime not available; install dependencies to run the local solver.", file=sys.stderr)
        return 2

    solver = GiftCaptchaSolver()
    if not solver.is_initialized:
        print("ONNX solver failed to initialize; ensure captcha_model.onnx and metadata are present.", file=sys.stderr)
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
            img_bytes = path.read_bytes()
            guess, solved, _method, conf, _ = solver.solve_captcha(img_bytes, fid=None, attempt=0)
            if solved and guess:
                ok += 1
                print(f"[{i}/{len(files)}] OK   {path.name:60s} -> {guess} (conf={conf:.3f})")
            else:
                fail += 1
                print(f"[{i}/{len(files)}] FAIL {path.name:60s} -> {guess or '-'}")
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(files)}] ERR  {path.name:60s} -> {e}")

    print(f"Done. Success={ok} Fail={fail} Total={len(files)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
