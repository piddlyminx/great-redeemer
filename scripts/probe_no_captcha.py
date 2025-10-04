from __future__ import annotations

import argparse
import json
import sys
from typing import Optional
from pathlib import Path

# Ensure repo root on path so `import wos_redeem` works when run directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Local import from repo
try:
    from wos_redeem import api
except Exception as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe /gift_code without captcha_code to observe server behavior")
    ap.add_argument("--fid", type=int, default=0, help="Player fid (use 0 for a non-existent user)")
    ap.add_argument("--cdk", default="TESTCODE", help="Gift code value to test (will likely be invalid)")
    ap.add_argument("--verbose", action="store_true", help="Print full JSON responses")
    args = ap.parse_args()

    fid: int = args.fid
    cdk: str = args.cdk

    print(f"Probing without captcha: fid={fid} cdk={cdk}")

    try:
        player = api.call_player(fid)
        print("/player →", player if args.verbose else {k: player.get(k) for k in ("code", "msg")})
    except Exception as e:
        print(f"/player error: {e}")

    print("Skipping /gift_code without captcha: flow disabled in this project.")
    print("Use the standard worker or redeem.py which solves captcha first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
