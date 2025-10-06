#!/usr/bin/env python3
"""
Clear all Gift Codes from the local database, including related redemptions
and attempts to preserve referential integrity.

Usage:
  uv run python scripts/clear_codes.py --yes

Respects DATABASE_URL; defaults to sqlite:///./wos.db
"""
from __future__ import annotations

import argparse
from sqlalchemy import select, func, delete
from sqlalchemy.exc import OperationalError

from wos_redeem.db import SessionLocal, GiftCode, Redemption, RedemptionAttempt


def count_rows(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Clear Gift Codes and related data")
    ap.add_argument("--yes", action="store_true", help="Do not prompt; proceed immediately")
    args = ap.parse_args()

    if not args.yes:
        print("This will delete ALL gift codes, redemptions, and attempts.")
        print("Re-run with --yes to confirm.")
        return

    try:
        with SessionLocal() as session:
            before = {
                "gift_codes": count_rows(session, GiftCode),
                "redemptions": count_rows(session, Redemption),
                "attempts": count_rows(session, RedemptionAttempt),
            }

            # Delete attempts → redemptions → gift codes
            session.execute(delete(RedemptionAttempt))
            session.execute(delete(Redemption))
            session.execute(delete(GiftCode))
            session.commit()

            after = {
                "gift_codes": count_rows(session, GiftCode),
                "redemptions": count_rows(session, Redemption),
                "attempts": count_rows(session, RedemptionAttempt),
            }
    except OperationalError as e:
        # Database not initialized yet; treat as already empty
        print("No gift_codes table found; database appears uninitialized. Nothing to delete.")
        return

    print("Deleted gift codes and related data.")
    print("Before:", before)
    print("After: ", after)


if __name__ == "__main__":
    main()
