from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _mk(db_sessionmaker):
    from wos_redeem.db import User, GiftCode

    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with db_sessionmaker() as s:
        # two active users
        s.add_all([User(fid=1, active=True), User(fid=2, active=True)])
        # one active code
        s.add(GiftCode(code="HELLO", active=True, source_created_at=created_at))
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
    from wos_redeem.tasks import _eligible_pairs
    from wos_redeem.db import SessionLocal, User, GiftCode, Redemption, RedemptionStatus

    with SessionLocal() as s:
        code = GiftCode(
            code="CODEX",
            active=True,
            source_created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
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


def test_reconcile_expires_missing_code_on_invalid_validation(db_sessionmaker):
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        code = GiftCode(code="HELLO", active=True, source_created_at=created_at)
        s.add(code)
        s.commit()

        _reconcile_gift_codes(s, [], now=now, validator=lambda *_: "invalid")
        s.refresh(code)
        assert code.active is False
        from wos_redeem.tasks import _as_utc
        assert _as_utc(code.expires_at) == now


def test_reconcile_keeps_missing_code_on_valid_validation(db_sessionmaker):
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        code = GiftCode(code="HELLO", active=True, source_created_at=created_at)
        s.add(code)
        s.commit()

        _reconcile_gift_codes(s, [], now=now, validator=lambda *_: "valid")
        s.refresh(code)
        assert code.active is True
        assert code.expires_at is None


def test_reconcile_reactivates_existing_code_date_on_valid_validation(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        existing = GiftCode(code="HELLO", active=False, source_created_at=created_at)
        s.add(existing)
        s.commit()

        _reconcile_gift_codes(
            s,
            [("HELLO", created_at)],
            now=created_at,
            validator=lambda *_: "valid",
        )
        codes = s.scalars(select(GiftCode).where(GiftCode.code == "HELLO")).all()
        assert len(codes) == 1
        assert codes[0].active is True


def test_reconcile_keeps_existing_code_inactive_on_invalid_validation(db_sessionmaker):
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        existing = GiftCode(code="HELLO", active=False, source_created_at=created_at)
        s.add(existing)
        s.commit()

        _reconcile_gift_codes(
            s,
            [("HELLO", created_at)],
            now=created_at,
            validator=lambda *_: "invalid",
        )
        s.refresh(existing)
        assert existing.active is False


def test_reconcile_allows_new_date_for_same_code(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    old_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    new_at = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with SessionLocal() as s:
        existing = GiftCode(code="HELLO", active=False, source_created_at=old_at)
        s.add(existing)
        s.commit()

        _reconcile_gift_codes(
            s,
            [("HELLO", new_at)],
            now=new_at,
            validator=lambda *_: "valid",
        )
        codes = s.scalars(select(GiftCode).where(GiftCode.code == "HELLO")).all()
        assert len(codes) == 2
        assert len([c for c in codes if c.active]) == 1


def test_reconcile_expires_old_active_code_when_new_date_added(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _reconcile_gift_codes, _as_utc
    from wos_redeem.db import SessionLocal, GiftCode

    old_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    new_at = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with SessionLocal() as s:
        existing = GiftCode(code="HELLO", active=True, source_created_at=old_at)
        s.add(existing)
        s.commit()
        existing_id = existing.id

        _reconcile_gift_codes(
            s,
            [("HELLO", new_at)],
            now=new_at,
            validator=lambda *_: "valid",
        )
        codes = s.scalars(select(GiftCode).where(GiftCode.code == "HELLO")).all()
        assert len(codes) == 2

        old_code = next(c for c in codes if c.id == existing_id)
        new_code = next(c for c in codes if c.id != existing_id)

        assert old_code.active is False
        assert _as_utc(old_code.expires_at) == new_at
        assert new_code.active is True
        assert _as_utc(new_code.source_created_at) == new_at


def test_reconcile_expires_codes_from_api_flag(db_sessionmaker):
    from wos_redeem.tasks import _reconcile_gift_codes
    from wos_redeem.db import SessionLocal, GiftCode

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        code1 = GiftCode(code="HELLO", active=True, source_created_at=created_at)
        other = GiftCode(code="HELLO", active=True, source_created_at=datetime(2025, 1, 2, tzinfo=timezone.utc))
        s.add_all([code1, other])
        s.commit()

        _reconcile_gift_codes(
            s,
            [("OTHER", created_at), ("HELLO", datetime(2025, 1, 2, tzinfo=timezone.utc))],
            expired_codes=[("HELLO", created_at)],
            now=now,
        )
        s.refresh(code1)
        s.refresh(other)

        assert code1.active is False
        assert other.active is True


def test_eligible_pairs_scan_beyond_first_batch(db_sessionmaker):
    from wos_redeem.tasks import _eligible_pairs
    from wos_redeem.db import SessionLocal, User, GiftCode

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        users = [User(fid=i, active=True) for i in range(1, 3)]
        s.add_all(users)
        # Create many active codes that will have no eligible pairs (users already redeemed)
        filler_codes = [
            GiftCode(code=f"FILLER{i}", active=True, source_created_at=base + timedelta(days=i))
            for i in range(25)
        ]
        target_code = GiftCode(code="NEWCODE", active=True, source_created_at=base + timedelta(days=100))
        s.add_all(filler_codes + [target_code])
        s.commit()
        # Mark filler codes as redeemed for all users
        from wos_redeem.db import Redemption, RedemptionStatus
        for c in filler_codes:
            for u in users:
                s.add(Redemption(user_id=u.id, gift_code_id=c.id, status=RedemptionStatus.redeemed_new.value))
        s.commit()

        pairs = _eligible_pairs(s, limit_codes=20, limit_users=5)
        codes_in_queue = {p.code for p in pairs}
        assert "NEWCODE" in codes_in_queue


def test_eligible_pairs_prioritizes_ark_alliance(db_sessionmaker):
    from wos_redeem.tasks import _eligible_pairs
    from wos_redeem.db import SessionLocal, User, GiftCode, Alliance

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as s:
        ark_alliance = Alliance(name="ARK Alliance", tag="ARK")
        other_alliance = Alliance(name="Other Alliance", tag="OTH")
        s.add_all([ark_alliance, other_alliance])
        s.commit()

        user_no_alliance = User(fid=1, active=True)
        user_other = User(fid=2, active=True, alliance_id=other_alliance.id)
        user_ark = User(fid=3, active=True, alliance_id=ark_alliance.id)
        s.add_all([user_no_alliance, user_other, user_ark])

        code = GiftCode(code="TESTCODE", active=True, source_created_at=base)
        s.add(code)
        s.commit()

        pairs = _eligible_pairs(s, limit_pairs=10)
        fids = [p.fid for p in pairs]

        assert fids[0] == 3  # ARK user should be first
        assert set(fids) == {1, 2, 3}


def test_pending_with_max_attempts_marked_failed(db_sessionmaker):
    from sqlalchemy import select
    from wos_redeem.tasks import _eligible_pairs, MAX_ATTEMPTS_PER_PAIR
    from wos_redeem.db import SessionLocal, User, GiftCode, Redemption, RedemptionStatus

    with SessionLocal() as s:
        user = User(fid=1, active=True)
        code = GiftCode(
            code="HELLO",
            active=True,
            source_created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        s.add_all([user, code])
        s.commit()
        s.add(
            Redemption(
                user_id=user.id,
                gift_code_id=code.id,
                status=RedemptionStatus.pending.value,
                attempt_count=MAX_ATTEMPTS_PER_PAIR,
            )
        )
        s.commit()

        pairs = _eligible_pairs(s, limit_codes=5, limit_users=5)
        assert pairs == []

        red = s.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
        assert red.status == RedemptionStatus.failed.value
