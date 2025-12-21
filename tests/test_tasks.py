from __future__ import annotations

import pytest


def _mk(db_sessionmaker):
    from wos_redeem.db import User, GiftCode
    with db_sessionmaker() as s:
        # two active users
        s.add_all([User(fid=1, active=True), User(fid=2, active=True)])
        # one active code
        s.add(GiftCode(code="HELLO", active=True))
        s.commit()


def test_eligible_count_basic(db_sessionmaker):
    from wos_redeem.tasks import eligible_count
    from wos_redeem.db import SessionLocal

    _mk(db_sessionmaker)
    with SessionLocal() as s:
        assert eligible_count(s) == 2  # 2 users x 1 code


def test_eligible_pairs_respects_success(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _eligible_pairs
    from wos_redeem.db import SessionLocal, User, GiftCode, Redemption, RedemptionStatus

    _mk(db_sessionmaker)
    with SessionLocal() as s:
        u1 = s.scalar(select(User).where(User.fid == 1))
        code = s.scalar(select(GiftCode).where(GiftCode.code == "HELLO"))
        # Mark one pair as already redeemed
        s.add(Redemption(user_id=u1.id, gift_code_id=code.id, status=RedemptionStatus.redeemed_new.value))
        s.commit()

        pairs = _eligible_pairs(s, limit_pairs=25)
        keys = {(p.fid, p.code) for p in pairs}
        assert (1, "HELLO") not in keys
        assert (2, "HELLO") in keys


def test_eligible_pairs_pages_past_first_200_users(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _eligible_pairs
    from wos_redeem.db import SessionLocal, User, GiftCode, Redemption, RedemptionStatus

    with SessionLocal() as s:
        code = GiftCode(code="CODEX", active=True)
        s.add(code)
        # 250 active users
        users = [User(fid=i, active=True) for i in range(1, 251)]
        s.add_all(users)
        s.commit()
        # Mark first 200 users as already redeemed (final status)
        for u in users[:200]:
            s.add(
                Redemption(
                    user_id=u.id,
                    gift_code_id=code.id,
                    status=RedemptionStatus.redeemed_new.value,
                )
            )
        s.commit()

        pairs = _eligible_pairs(s, limit_pairs=1000)
        fids = {p.fid for p in pairs}

        assert 1 not in fids  # first batch is final and excluded
        assert 200 not in fids
        assert 201 in fids  # users beyond the first 200 are surfaced
        assert 250 in fids
