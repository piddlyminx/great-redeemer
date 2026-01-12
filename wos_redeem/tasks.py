from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import random
from typing import Optional, List, Callable
import os
import json
import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy import select, func, exists, literal, update
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal, GiftCode, User, Redemption, RedemptionAttempt, RedemptionStatus
from . import api
from .captcha_solver import GiftCaptchaSolver
from .utils import save_failure_captcha, _data_url_to_bytes
from .queueing import worker_state, QueueItem


"""Constants

Environment overrides are supported for all runtime tunables. Keep names short
and consistent. Numbers are seconds unless noted.
"""

# Queue watermarks and attempt limits
REDEEM_QUEUE_MIN = int(os.getenv("REDEEM_QUEUE_MIN", "5"))
REDEEM_QUEUE_MAX = int(os.getenv("REDEEM_QUEUE_MAX", "20"))
MAX_ATTEMPTS_PER_PAIR = int(os.getenv("REDEEM_MAX_ATTEMPTS_PER_PAIR", "3"))

# Timing/backoff
ATTEMPT_DELAY_S = float(os.getenv("REDEEM_DELAY_S", "4"))
JITTER_FRAC = float(os.getenv("REDEEM_JITTER_FRAC", "0.5"))  # fraction of base delay used as +jitter
MIN_RETRY_MINUTES = int(os.getenv("REDEEM_MIN_RETRY_MINUTES", "15"))
REDEEM_POLL_SECONDS = int(os.getenv("REDEEM_POLL_SECONDS", "20"))
REDEEM_OUTER_RETRIES = int(os.getenv("REDEEM_OUTER_RETRIES", "2"))
# Inner retry budget per item (CAPTCHA loop)
REDEEM_INNER_RETRIES = int(os.getenv("REDEEM_INNER_RETRIES", "3"))

# Status/logging
STATUS_DIR = os.getenv("STATUS_DIR", "")  # Where small heartbeat/status files are written
LOG_DIR = os.getenv("LOG_DIR", "logs")

# Gift code source (listing API)
CODE_SOURCE_URL = "https://gift-code-api.whiteout-bot.com/giftcode_api.php"
# Per request: this API is not a secret; keep as a constant.
CODE_SOURCE_API_KEY = "super_secret_bot_token_nobody_will_ever_find"
CODE_FETCH_TIMEOUT_S = float(os.getenv("CODE_FETCH_TIMEOUT_S", "15"))
CODE_FETCH_INTERVAL_S = int(os.getenv("CODE_FETCH_INTERVAL_S", "300"))
CODE_FETCH_UA = os.getenv(
    "CODE_FETCH_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)

CODE_VALIDATE_FID = int(os.getenv("CODE_VALIDATE_FID", "244886619"))
CODE_VALIDATE_OUTER_RETRIES = int(os.getenv("CODE_VALIDATE_OUTER_RETRIES", "1"))
CODE_VALIDATE_INNER_RETRIES = int(os.getenv("CODE_VALIDATE_INNER_RETRIES", "2"))
CODE_VALIDATE_SLEEP = os.getenv("CODE_VALIDATE_SLEEP", "0") in ("1", "true", "True")



def _status_path(name: str) -> str:
    base = STATUS_DIR or "."
    return os.path.join(base, name)


def _log_setup() -> logging.Logger:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass
    logger = logging.getLogger("redeemer_worker")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        try:
            fh = RotatingFileHandler(os.path.join(LOG_DIR, "worker.log"), maxBytes=1_000_000, backupCount=3)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(fh)
        except Exception:
            # Last resort: log to stderr
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(sh)
    return logger


LOGGER = _log_setup()


class RedeemOutcome(str, Enum):
    redeemed_new = "redeemed_new"
    redeemed_already = "redeemed_already"
    vip_required = "vip_required"
    invalid = "invalid"
    transient = "transient"
    unknown = "unknown"


@dataclass
class RedeemAttemptResult:
    outcome: RedeemOutcome
    msg: Optional[str]
    msg_norm: str
    err_code: Optional[int]
    captcha: Optional[str]
    notes: list[str]
    error_count: int


VALIDATION_VALID = "valid"
VALIDATION_INVALID = "invalid"
VALIDATION_TRANSIENT = "transient"


def _normalize_gift_code_msg(msg: object) -> str:
    return (str(msg) if isinstance(msg, str) else "").strip().rstrip(".").upper()


def _classify_gift_code_response(status_code: object, msg_norm: str) -> RedeemOutcome:
    if msg_norm in {"SUCCESS"} or status_code == 0:
        return RedeemOutcome.redeemed_new
    if msg_norm in {"RECEIVED", "SAME TYPE EXCHANGE", "ALREADY RECEIVED"}:
        return RedeemOutcome.redeemed_already
    if msg_norm in {"CDK NOT FOUND", "USED", "TIME ERROR"}:
        return RedeemOutcome.invalid
    if "VIP" in msg_norm or "RECHARGE" in msg_norm:
        return RedeemOutcome.vip_required
    if msg_norm in {"NOT LOGIN", "TIMEOUT RETRY", "CAPTCHA CHECK ERROR", "CAPTCHA CHECK TOO FREQUENT"}:
        return RedeemOutcome.transient
    return RedeemOutcome.unknown


def _validation_outcome_from_redeem(outcome: RedeemOutcome) -> str:
    if outcome in {RedeemOutcome.redeemed_new, RedeemOutcome.redeemed_already, RedeemOutcome.vip_required}:
        return VALIDATION_VALID
    if outcome == RedeemOutcome.invalid:
        return VALIDATION_INVALID
    return VALIDATION_TRANSIENT


def _redeem_with_solver(
    fid: int,
    code: str,
    solver: GiftCaptchaSolver,
    outer_retries: int,
    inner_retries: int,
    *,
    sleep_backoff: bool = True,
    log_fn: Callable[[str], None] | None = None,
    context: str = "worker",
) -> RedeemAttemptResult:
    def _log(message: str) -> None:
        if log_fn:
            log_fn(f"[{context}] {message}")

    notes: list[str] = []
    last_err_code: Optional[int] = None
    last_captcha: Optional[str] = None
    last_msg: Optional[str] = None
    last_msg_norm = ""
    error_count = 0
    outcome = RedeemOutcome.transient

    for outer_i in range(1, max(1, outer_retries) + 1):
        # Ensure /player session (profile)
        try:
            prof = api.call_player(fid)
            if prof.get("code") != 0:
                raise RuntimeError(f"/player nonzero code: {prof}")
            _log(f"fid={fid} /player ok")
        except Exception as e:
            note = f"outer#{outer_i} /player error: {e}"
            notes.append(note)
            _log(note)
            if sleep_backoff:
                _sleep_backoff(1.0)
            continue

        inner_i = 0
        while inner_i < max(1, inner_retries):
            inner_i += 1
            # Always fetch captcha; backend requires it as of 2025-10-04.
            try:
                cap = api.call_captcha(fid)
                data_url = cap.get("data", {}).get("img")
                if not isinstance(data_url, str):
                    raise RuntimeError("captcha response missing image data")
                _log(f"fid={fid} /captcha ok")
            except Exception as e:
                note = f"outer#{outer_i} inner#{inner_i} /captcha error: {e}"
                notes.append(note)
                _log(note)
                error_count += 1
                if sleep_backoff:
                    _sleep_backoff(1.0)
                continue

            # Solve via local ONNX model
            try:
                img_bytes, _ext = _data_url_to_bytes(data_url)
                guess, ok, method, conf, _ = solver.solve_captcha(
                    img_bytes, fid=fid, attempt=(inner_i - 1)
                )
                if not ok or not guess:
                    raise RuntimeError("onnx_solver_no_guess")
                captcha = str(guess)
                last_captcha = captcha
                _log(f"fid={fid} onnx captcha={captcha} conf={conf:.3f}")
            except Exception as e:
                _log(f"fid={fid} onnx solver error: {e}")
                try:
                    out_path = save_failure_captcha(
                        data_url,
                        fid=fid,
                        guess="none",
                        reason="onnx_error",
                    )
                    _log(f"saved failed captcha to: {out_path}")
                except Exception:
                    pass
                notes.append(f"outer#{outer_i} inner#{inner_i} onnx solver error: {e}")
                error_count += 1
                if sleep_backoff:
                    _sleep_backoff(1.0)
                continue

            # Redeem with captcha
            resp = api.call_gift_code(fid, code, captcha)
            status_code = resp.get("code")
            msg = resp.get("msg")
            err_code = resp.get("err_code")
            last_err_code = int(err_code) if isinstance(err_code, int) else None
            last_msg = str(msg) if msg is not None else None
            last_msg_norm = _normalize_gift_code_msg(msg)
            _log(f"fid={fid} /gift_code code={status_code} msg={msg} err={err_code}")

            outcome = _classify_gift_code_response(status_code, last_msg_norm)
            if outcome in {
                RedeemOutcome.redeemed_new,
                RedeemOutcome.redeemed_already,
                RedeemOutcome.invalid,
                RedeemOutcome.vip_required,
            }:
                return RedeemAttemptResult(
                    outcome=outcome,
                    msg=last_msg,
                    msg_norm=last_msg_norm,
                    err_code=last_err_code,
                    captcha=last_captcha,
                    notes=notes,
                    error_count=error_count,
                )

            if last_msg_norm in {"NOT LOGIN", "TIMEOUT RETRY"}:
                note = f"outer#{outer_i} inner#{inner_i} backend={last_msg_norm} -> retry outer"
                notes.append(note)
                _log(note)
                break

            if last_msg_norm == "CAPTCHA CHECK ERROR":
                note = f"outer#{outer_i} inner#{inner_i} captcha wrong; will retry"
                _log(note)
                notes.append(note)
                try:
                    LOGGER.info(json.dumps({"event": "captcha_check_error", "fid": fid, "code": code}))
                except Exception:
                    pass
                try:
                    out_path = save_failure_captcha(data_url, fid=fid, guess=captcha, reason="captcha_check_error")
                    _log(f"saved failed captcha to: {out_path}")
                except Exception:
                    pass
                error_count += 1
                if sleep_backoff:
                    _sleep_backoff(1.0)
                continue

            if last_msg_norm == "CAPTCHA CHECK TOO FREQUENT":
                try:
                    LOGGER.info(json.dumps({"event": "captcha_too_frequent", "fid": fid, "code": code}))
                except Exception:
                    pass
                notes.append(f"outer#{outer_i} inner#{inner_i} captcha too frequent; retrying")
                error_count += 1
                if sleep_backoff:
                    _sleep_backoff(2.0)
                continue

            notes.append(f"outer#{outer_i} inner#{inner_i} unknown msg={last_msg_norm}")
            error_count += 1
            if sleep_backoff:
                _sleep_backoff(1.0)

    return RedeemAttemptResult(
        outcome=outcome,
        msg=last_msg,
        msg_norm=last_msg_norm,
        err_code=last_err_code,
        captcha=last_captcha,
        notes=notes,
        error_count=error_count,
    )


def _parse_code_created_at(raw: str) -> datetime | None:
    """Parse API creation dates in dd.mm.yyyy format."""
    try:
        parsed = datetime.strptime(raw.strip(), "%d.%m.%Y")
    except Exception:
        return None
    return parsed


def _normalize_source_created_at(value: datetime | None) -> datetime | None:
    """Normalize source_created_at to a naive UTC datetime for comparisons."""
    if value is None:
        return None
    normalized = _as_utc(value) or value
    return normalized.replace(tzinfo=None)


def _validate_code_with_redeem(
    code_value: str,
    source_created_at: datetime,
    solver: GiftCaptchaSolver,
) -> str:
    if not solver or not getattr(solver, "is_initialized", False):
        return VALIDATION_TRANSIENT
    try:
        result = _redeem_with_solver(
            CODE_VALIDATE_FID,
            code_value,
            solver,
            CODE_VALIDATE_OUTER_RETRIES,
            CODE_VALIDATE_INNER_RETRIES,
            sleep_backoff=CODE_VALIDATE_SLEEP,
            log_fn=None,
            context="code-validate",
        )
        return _validation_outcome_from_redeem(result.outcome)
    except Exception:
        return VALIDATION_TRANSIENT


def _fetch_codes_from_api() -> tuple[list[tuple[str, datetime]], list[tuple[str, datetime]]]:
    """Fetch gift codes from the listing API.

    Expects a JSON body like: {"codes": ["CODE1 27.10.2025", "CODE2 26.10.2025"]}.
    The trailing date is the creation date (not expiry) and is stored.

    If the API provides per-code status fields, expired codes are returned
    separately for reconciliation.
    """
    import requests

    headers = {
        "X-API-Key": CODE_SOURCE_API_KEY,
        "Accept": "application/json",
        "User-Agent": CODE_FETCH_UA,
    }
    try:
        r = requests.get(CODE_SOURCE_URL, headers=headers, timeout=CODE_FETCH_TIMEOUT_S)
        r.raise_for_status()
    except Exception as e:
        LOGGER.info("code_fetch: request error: %s", e)
        return [], []

    try:
        payload = r.json()
    except Exception:
        LOGGER.info("code_fetch: non-JSON response")
        return [], []

    raw_list = payload.get("codes") if isinstance(payload, dict) else None
    if not isinstance(raw_list, list):
        return [], []

    statuses: dict[tuple[str, datetime], bool] = {}
    for item in raw_list:
        code = None
        created_at = None
        active_flag = True
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            parts = text.split()
            if len(parts) < 2:
                continue
            code = parts[0]
            created_at = _parse_code_created_at(parts[1])
        elif isinstance(item, dict):
            raw_code = item.get("code") or item.get("cdk") or item.get("gift_code")
            if isinstance(raw_code, str):
                raw_code = raw_code.strip()
                code = raw_code.split()[0] if raw_code else None
            raw_date = item.get("created_at") or item.get("created") or item.get("date")
            if isinstance(raw_date, str):
                created_at = _parse_code_created_at(raw_date.split()[0])
            elif isinstance(raw_date, datetime):
                created_at = _normalize_source_created_at(raw_date)

            active_val = item.get("active")
            expired_val = item.get("expired")
            status_val = item.get("status") or item.get("state")
            if isinstance(active_val, bool):
                active_flag = active_val
            elif isinstance(expired_val, bool):
                active_flag = not expired_val
            elif isinstance(status_val, str):
                status_norm = status_val.strip().lower()
                if status_norm in {"expired", "inactive", "invalid"}:
                    active_flag = False
                elif status_norm in {"active", "valid", "live"}:
                    active_flag = True
        else:
            continue

        created_at = _normalize_source_created_at(created_at)
        if not code or not created_at:
            continue
        if not re.fullmatch(r"[A-Za-z0-9]{4,24}", code):
            continue

        key = (code, created_at)
        if active_flag is False:
            statuses[key] = False
        elif key not in statuses:
            statuses[key] = True

    active_codes = [key for key, active in statuses.items() if active]
    expired_codes = [key for key, active in statuses.items() if not active]
    return active_codes, expired_codes


def _as_utc(dt: datetime | None) -> datetime | None:
    """Return dt as timezone-aware UTC.

    DB may persist naive datetimes; comparisons against aware now/UTC must
    normalize to avoid TypeError: naive vs aware.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except Exception:
        # Fallback defensively; better to treat as UTC than crash the worker
        return dt.replace(tzinfo=timezone.utc)


def _expire_code(code: GiftCode, now: datetime | None = None) -> None:
    """Mark a code inactive and record the expiration time."""
    now = now or datetime.now(timezone.utc)
    code.active = False
    code.expires_at = now


def _expire_matching_codes(
    db: Session,
    code_value: str,
    source_created_at: datetime | None = None,
    now: datetime | None = None,
) -> int:
    """Expire all active rows that match the given code value (and date, if provided)."""
    now = now or datetime.now(timezone.utc)
    source_created_at = _normalize_source_created_at(source_created_at)
    stmt = select(GiftCode).where(GiftCode.code == code_value, GiftCode.active == True)
    if source_created_at is not None:
        stmt = stmt.where(GiftCode.source_created_at == source_created_at)
    matches = db.scalars(stmt).all()
    for gc in matches:
        _expire_code(gc, now=now)
    return len(matches)


def _reconcile_gift_codes(
    db: Session,
    codes: list[tuple[str, datetime]],
    expired_codes: list[tuple[str, datetime]] | None = None,
    now: datetime | None = None,
    validator: Callable[[str, datetime], str] | None = None,
) -> None:
    """Sync gift codes based on the latest API response.

    - Deactivate active codes missing from the API when validation says invalid.
    - Insert or reactivate codes only if validation says they are valid.
    """
    now = now or datetime.now(timezone.utc)
    normalized_codes = [
        (code, norm)
        for code, created_at in (codes or [])
        if (norm := _normalize_source_created_at(created_at)) is not None
    ]
    normalized_expired = [
        (code, norm)
        for code, created_at in (expired_codes or [])
        if (norm := _normalize_source_created_at(created_at)) is not None
    ]
    expired_set = set(normalized_expired)
    code_set = set(normalized_codes) - expired_set
    active_codes = db.scalars(select(GiftCode).where(GiftCode.active == True)).all()

    changed = False
    validation_cache: dict[tuple[str, datetime], str] = {}

    def _validate(code_value: str, created_at: datetime) -> str:
        key = (code_value, created_at)
        cached = validation_cache.get(key)
        if cached:
            return cached
        if validator is None:
            validation_cache[key] = VALIDATION_TRANSIENT
            return validation_cache[key]
        outcome = validator(code_value, created_at)
        validation_cache[key] = outcome
        return outcome

    # Expire codes explicitly flagged as expired by the source
    for code_value, created_at in expired_set:
        if _expire_matching_codes(db, code_value, source_created_at=created_at, now=now):
            changed = True

    # Validate active codes missing from the API
    for gc in active_codes:
        normalized_created_at = _normalize_source_created_at(gc.source_created_at)
        if normalized_created_at is None:
            continue
        key = (gc.code, normalized_created_at)
        if key in code_set or key in expired_set:
            continue
        outcome = _validate(gc.code, normalized_created_at)
        if outcome == VALIDATION_INVALID:
            _expire_code(gc, now=now)
            changed = True

    # Add or reactivate codes for unseen (code, created_at) pairs
    seen_codes: set[tuple[str, datetime]] = set()
    for code_value, created_at in normalized_codes:
        key = (code_value, created_at)
        if key in expired_set or key in seen_codes:
            continue
        seen_codes.add(key)
        existing = db.scalar(
            select(GiftCode).where(
                GiftCode.code == code_value,
                GiftCode.source_created_at == created_at,
            )
        )
        if existing:
            if not existing.active and _validate(code_value, created_at) == VALIDATION_VALID:
                existing.active = True
                existing.expires_at = None
                changed = True
            continue

        if _validate(code_value, created_at) != VALIDATION_VALID:
            continue

        # Expire any existing active codes with the same value but different date
        old_codes = db.scalars(
            select(GiftCode).where(
                GiftCode.code == code_value,
                GiftCode.source_created_at != created_at,
                GiftCode.active == True,
            )
        ).all()
        for old_code in old_codes:
            old_code.active = False
            old_code.expires_at = now
            changed = True

        db.add(
            GiftCode(
                code=code_value,
                source_created_at=created_at,
                source_url=CODE_SOURCE_URL,
            )
        )
        changed = True

    if changed:
        db.commit()


def code_fetch_loop(interval_seconds: int | None = None) -> None:
    """Background loop that polls the code source API and stores new codes.

    Writes a simple heartbeat file `.codes_heartbeat` with the start time of the
    last poll. Synchronizes gift codes by validating missing/new codes and
    inserting newly seen (code, created_at) pairs with `source_url` set to
    CODE_SOURCE_URL.
    """
    period = int(interval_seconds or CODE_FETCH_INTERVAL_S)
    onnx_solver: GiftCaptchaSolver | None = None
    while True:
        started = datetime.now(timezone.utc)
        try:
            if not onnx_solver or not getattr(onnx_solver, "is_initialized", False):
                try:
                    onnx_solver = GiftCaptchaSolver()
                except Exception:
                    onnx_solver = None
            codes, expired_codes = _fetch_codes_from_api()
            validator = None
            if onnx_solver and getattr(onnx_solver, "is_initialized", False):
                def _validator(code_value: str, created_at: datetime) -> str:
                    return _validate_code_with_redeem(code_value, created_at, onnx_solver)
                validator = _validator
            with SessionLocal() as db:
                _reconcile_gift_codes(
                    db,
                    codes,
                    expired_codes=expired_codes,
                    now=started,
                    validator=validator,
                )
        except Exception:
            LOGGER.exception("code_fetch_loop_error")
        try:
            with open(_status_path(".codes_heartbeat"), "w") as f:
                f.write(started.isoformat())
        except Exception:
            pass
        time.sleep(max(5, period))


def eligible_count(db: Session) -> int:
    """Count eligible (user, code) pairs below cap and outside backoff.

    Uses a cross-count with NOT EXISTS filters to avoid Python-side cartesian loops.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.now(timezone.utc)
    # Treat any terminal outcome as ineligible.
    final_like = list(RedemptionStatus.final_statuses())
    u = User.__table__.alias("u_ec")
    c = GiftCode.__table__.alias("c_ec")
    stmt = (
        select(func.count())
        .select_from(u.join(c, literal(True)))
        .where(u.c.active == True, c.c.active == True)
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == u.c.id,
            Redemption.gift_code_id == c.c.id,
            Redemption.status.in_(final_like),
        )))
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == u.c.id,
            Redemption.gift_code_id == c.c.id,
            Redemption.attempt_count >= MAX_ATTEMPTS_PER_PAIR,
        )))
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == u.c.id,
            Redemption.gift_code_id == c.c.id,
            Redemption.last_attempt_at.is_not(None),
            Redemption.last_attempt_at > cutoff,
        )))
    )
    return int(db.scalar(stmt) or 0)


def _eligible_pairs(
    db: Session,
    limit_pairs: int | None = None,
    limit_codes: int | None = None,
    limit_users: int | None = None,
) -> List[QueueItem]:
    """Return eligible (user, code) pairs ready to process now.

    Applies: active flags, success filter, retry backoff window, and max attempts per pair.
    Ordered by code first_seen_at asc, then user id asc. The result set is limited
    at the pair level (`limit_pairs`).
    """
    if limit_pairs is None:
        if limit_codes is not None or limit_users is not None:
            limit_pairs = max(1, (limit_codes or 20) * (limit_users or 200))
        else:
            limit_pairs = 4000
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.now(timezone.utc)
    final_like = list(RedemptionStatus.final_statuses())
    u = User.__table__.alias("u")
    c = GiftCode.__table__.alias("c")
    r = Redemption.__table__.alias("r")
    stmt = (
        select(
            u.c.id.label("user_id"),
            u.c.fid,
            u.c.name,
            c.c.id.label("gift_code_id"),
            c.c.code,
        )
        .select_from(u.join(c, literal(True)))
        .where(u.c.active == True, c.c.active == True)
        .where(
            ~exists(
                select(r.c.id).where(
                    r.c.user_id == u.c.id,
                    r.c.gift_code_id == c.c.id,
                    r.c.status.in_(final_like),
                )
            )
        )
        .where(
            ~exists(
                select(r.c.id).where(
                    r.c.user_id == u.c.id,
                    r.c.gift_code_id == c.c.id,
                    r.c.attempt_count >= MAX_ATTEMPTS_PER_PAIR,
                )
            )
        )
        .where(
            ~exists(
                select(r.c.id).where(
                    r.c.user_id == u.c.id,
                    r.c.gift_code_id == c.c.id,
                    r.c.last_attempt_at.is_not(None),
                    r.c.last_attempt_at > cutoff,
                )
            )
        )
        .order_by(c.c.first_seen_at.asc(), u.c.id.asc())
        .limit(max(1, limit_pairs))
    )

    rows = db.execute(stmt).all()
    out = [
        QueueItem(
            user_id=row.user_id,
            fid=row.fid,
            name=row.name,
            gift_code_id=row.gift_code_id,
            code=row.code,
        )
        for row in rows
    ]
    try:
        result = db.execute(
            update(Redemption)
            .where(
                Redemption.status == RedemptionStatus.pending.value,
                Redemption.attempt_count >= MAX_ATTEMPTS_PER_PAIR,
            )
            .values(status=RedemptionStatus.failed.value)
        )
        if result.rowcount:
            db.commit()
    except Exception:
        db.rollback()
    return out


def _refill_queue(db: Session) -> int:
    """Top up the in-memory queue when below low-water mark.

    Returns the number of items added (up to REDEEM_QUEUE_MAX total).
    """
    cur = len(worker_state.queue)
    if cur >= REDEEM_QUEUE_MIN:
        return 0
    need = max(0, REDEEM_QUEUE_MAX - cur)
    if need == 0:
        return 0
    pairs = _eligible_pairs(db)
    return worker_state.add_unique(pairs[:need])


def _sleep_backoff(multiplier: float = 1.0) -> None:
    """Sleep for ATTEMPT_DELAY_S * multiplier plus a small positive jitter.

    Jitter defaults to `JITTER_FRAC` of the base. Example: with ATTEMPT_DELAY_S=4s and multiplier=2,
    base=8s and jitter in [0, 2s].
    """
    base = max(0.0, ATTEMPT_DELAY_S * multiplier)
    if base <= 0:
        return
    jitter = random.uniform(0.0, base * max(0.0, JITTER_FRAC))
    time.sleep(base + jitter)


def redemption_worker_loop(openrouter_api_key_env: str = "OPENROUTER_API_KEY", max_attempts_per_pair: int = None, poll_seconds: int = None) -> None:
    if max_attempts_per_pair is None:
        max_attempts_per_pair = MAX_ATTEMPTS_PER_PAIR
    if poll_seconds is None:
        poll_seconds = REDEEM_POLL_SECONDS
    # Initialize ONNX solver once per worker
    try:
        onnx_solver = GiftCaptchaSolver()
    except Exception:
        onnx_solver = None  # type: ignore[assignment]
    while True:
        attempts_made = 0
        successes = 0
        errors = 0
        try:
            # If ONNX solver is not ready, idle but keep heartbeats so UI shows liveness
            if not onnx_solver or not getattr(onnx_solver, "is_initialized", False):
                try:
                    with SessionLocal() as db_ec:
                        ec = eligible_count(db_ec)
                    with open(_status_path(".worker_heartbeat"), "w") as f:
                        f.write(datetime.now(timezone.utc).isoformat())
                    status = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "attempts": attempts_made,
                        "successes": successes,
                        "errors": errors,
                        "sleep": poll_seconds,
                        "note": "onnx_solver_unavailable",
                        "current": None,
                        "eligible": ec,
                    }
                    with open(_status_path(".worker_status"), "w") as f:
                        f.write(json.dumps(status))
                except Exception:
                    pass
                print(f"[worker] ONNX solver unavailable; sleeping {poll_seconds}s", flush=True)
                time.sleep(poll_seconds)
                continue
            with SessionLocal() as db:
                # Top up the work queue
                added = _refill_queue(db)
                if added:
                    print(f"[worker] queued {added} pairs", flush=True)
                # Legacy RECEIVED reconciliation removed; handled once at startup.
                # Drain queue for this cycle
                stop_cycle = False
                while True:
                    if stop_cycle:
                        break
                    item = worker_state.pop()
                    if item is None:
                        break
                    user = db.get(User, item.user_id)
                    code = db.get(GiftCode, item.gift_code_id)
                    if not user or not code or not user.active or not code.active:
                        continue
                    redemption = db.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
                    if redemption and RedemptionStatus.is_final(redemption.status):
                        continue
                    if not redemption:
                        # Handle race condition: another worker may have created this redemption
                        try:
                            redemption = Redemption(user_id=user.id, gift_code_id=code.id)
                            db.add(redemption)
                            db.commit()
                            db.refresh(redemption)
                        except IntegrityError:
                            # Another worker created this redemption between our check and insert
                            db.rollback()
                            print(f"[worker] race condition: redemption for fid={user.fid} code={code.code} created by another worker", flush=True)
                            redemption = db.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
                            if not redemption:
                                # Redemption disappeared; skip this pair
                                continue

                    # Skip if we've already tried the max attempts for this pair
                    if redemption.attempt_count >= max_attempts_per_pair:
                        if redemption.status == RedemptionStatus.pending.value:
                            redemption.status = RedemptionStatus.failed.value
                            db.commit()
                        continue

                    # Respect backoff
                    if redemption.last_attempt_at:
                        last = _as_utc(redemption.last_attempt_at)
                        cutoff_backoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_RETRY_MINUTES)
                        if last and last > cutoff_backoff:
                            continue

                    print(f"[worker] fid={user.fid} code={code.code} starting attempt #{redemption.attempt_count + 1}")
                    # Update lightweight current status for UI peek
                    try:
                        import json as _json
                        status_patch = {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "current": {"fid": user.fid, "user_id": user.id, "code": code.code, "gift_code_id": code.id},
                            "queue": [
                                {"fid": q.fid, "user_id": q.user_id, "name": q.name, "code": q.code, "gift_code_id": q.gift_code_id}
                                for q in worker_state.snapshot(10)
                            ],
                        }
                        try:
                            with open(_status_path(".worker_status")) as f:
                                cur = _json.loads(f.read())
                        except Exception:
                            cur = {}
                        cur.update(status_patch)
                        with open(_status_path(".worker_status"), "w") as f:
                            f.write(_json.dumps(cur))
                    except Exception:
                        pass

                    # New single-attempt semantics with outer/inner retries
                    attempt_no = redemption.attempt_count + 1
                    stop_this_cycle = False

                    result = _redeem_with_solver(
                        user.fid,
                        code.code,
                        onnx_solver,
                        REDEEM_OUTER_RETRIES,
                        REDEEM_INNER_RETRIES,
                        sleep_backoff=True,
                        log_fn=lambda msg: print(msg, flush=True),
                        context="worker",
                    )
                    attempt_notes = result.notes
                    last_err_code = result.err_code
                    last_captcha = result.captcha
                    errors += result.error_count

                    if result.outcome == RedeemOutcome.redeemed_new:
                        redemption.status = RedemptionStatus.redeemed_new.value
                        redemption.captcha = last_captcha
                        redemption.result_msg = result.msg or ""
                        successes += 1
                    elif result.outcome == RedeemOutcome.redeemed_already:
                        redemption.status = RedemptionStatus.redeemed_already.value
                        redemption.captcha = last_captcha
                        redemption.result_msg = result.msg or ""
                        successes += 1
                    elif result.outcome == RedeemOutcome.invalid:
                        # Globally invalid/expired/consumed code — deactivate & drain queue
                        try:
                            _expire_matching_codes(
                                db,
                                code.code,
                                source_created_at=code.source_created_at,
                            )
                            db.commit()
                            worker_state.clear()
                            _refill_queue(db)
                            print(
                                f"[worker] code={code.code} marked inactive due to '{result.msg_norm}'. queue reset+refill",
                                flush=True,
                            )
                        except Exception:
                            db.rollback()
                        if redemption.status not in (RedemptionStatus.redeemed_new.value, RedemptionStatus.redeemed_already.value):
                            redemption.status = RedemptionStatus.failed.value
                        redemption.captcha = last_captcha
                        redemption.result_msg = result.msg or ""
                        errors += 1
                        stop_this_cycle = True
                    elif result.outcome == RedeemOutcome.vip_required:
                        redemption.status = RedemptionStatus.failed.value
                        redemption.captcha = last_captcha
                        redemption.result_msg = result.msg or ""
                        errors += 1
                        print(
                            f"[worker] fid={user.fid} code={code.code} failed due to VIP level requirement: {result.msg_norm}",
                            flush=True,
                        )


                    # End outer loop: persist exactly one RedemptionAttempt for this cycle
                    try:
                        result_payload = {
                            "outcome": redemption.status,
                            "notes": attempt_notes[-20:],
                        }
                        att = RedemptionAttempt(
                            redemption_id=redemption.id,
                            attempt_no=attempt_no,
                            captcha=last_captcha,
                            result_msg=json.dumps(result_payload)[:1000],  # type: ignore[name-defined]
                            err_code=last_err_code,
                        )
                        db.add(att)
                        redemption.err_code = last_err_code
                        redemption.last_attempt_at = datetime.now(timezone.utc)
                        redemption.attempt_count += 1
                        if redemption.attempt_count >= max_attempts_per_pair and redemption.status == RedemptionStatus.pending.value:
                            redemption.status = RedemptionStatus.failed.value
                        db.commit()
                        attempts_made += 1
                    except Exception as db_err:
                        db.rollback()
                        print(f"[worker] fid={user.fid} DB error persisting single-attempt result: {db_err}", flush=True)
                        errors += 1

                    # Inter-item delay before attempting the next queue item
                    if ATTEMPT_DELAY_S > 0:
                        delay = max(0.0, ATTEMPT_DELAY_S + random.uniform(-0.5, 0.5))
                        time.sleep(delay)
                    # Signal cycle stop if we deactivated code
                    if stop_this_cycle:
                        stop_cycle = True
        except Exception:
            LOGGER.exception("unexpected_exception_in_main_loop")
        
        # heartbeat file
        with open(_status_path(".worker_heartbeat"), "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())

        # Write compact status for UI
        try:
            with SessionLocal() as db2:
                ec = eligible_count(db2)
            status = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "attempts": attempts_made,
                "successes": successes,
                "errors": errors,
                "sleep": poll_seconds,
                "current": None,  # clear any stale current indicator between cycles
                "queue_size": len(worker_state.queue),
                "queue": [
                    {"fid": q.fid, "user_id": q.user_id, "name": q.name, "code": q.code, "gift_code_id": q.gift_code_id}
                    for q in worker_state.snapshot(10)
                ],
                "eligible": ec,
            }
            with open(_status_path(".worker_status"), "w") as f:
                f.write(json.dumps(status))
        except Exception:
            pass
        print(f"[worker] idle; sleeping {poll_seconds}s", flush=True)
        time.sleep(poll_seconds)


def start_background_threads() -> None:
    t1 = threading.Thread(target=code_fetch_loop, name="code-fetcher", daemon=True)
    t1.start()
    t2 = threading.Thread(target=redemption_worker_loop, name="redeem-worker", daemon=True)
    t2.start()
