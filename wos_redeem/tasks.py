from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
import random
from typing import Optional, List
import os
import json
import logging
from logging.handlers import RotatingFileHandler

from sqlalchemy import select, func, exists, and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .db import SessionLocal, GiftCode, User, Redemption, RedemptionAttempt, RedemptionStatus
from . import api
from .solver import solve_captcha_via_openrouter, CaptchaSolverError
from .utils import save_failure_captcha
from .queueing import worker_state, QueueItem


# Queue watermarks and attempt limits
REDEEM_QUEUE_MIN = int(os.getenv("REDEEM_QUEUE_MIN", "5"))
REDEEM_QUEUE_MAX = int(os.getenv("REDEEM_QUEUE_MAX", "20"))
MAX_ATTEMPTS_PER_PAIR = int(os.getenv("REDEEM_MAX_ATTEMPTS_PER_PAIR", "3"))
# Timing
ATTEMPT_DELAY_S = float(os.getenv("REDEEM_DELAY_S", "4"))
JITTER_FRAC = float(os.getenv("REDEEM_JITTER_FRAC", "0.5"))  # fraction of base delay used as +jitter
MIN_RETRY_MINUTES = int(os.getenv("REDEEM_MIN_RETRY_MINUTES", "15"))
REDEEM_POLL_SECONDS = int(os.getenv("REDEEM_POLL_SECONDS", "20"))
REDEEM_OUTER_RETRIES = int(os.getenv("REDEEM_OUTER_RETRIES", "2"))
# Inner retry budget per item (CAPTCHA loop)
REDEEM_INNER_RETRIES = int(os.getenv("REDEEM_INNER_RETRIES", 3))

# Where to write small heartbeat/status files so the API can read them.
# Defaults to current working directory; set to a shared volume path like "/state" in compose.
STATUS_DIR = os.getenv("STATUS_DIR", "")
LOG_DIR = os.getenv("LOG_DIR", "logs")


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



def extract_codes(text: str) -> list[str]:
    # Simple heuristic: uppercase letters/digits 6-16 length, plus common WOS patterns
    return re.findall(r"\b[A-Z0-9]{6,16}\b", text.upper())


def _scrape_wosrewards_active() -> list[str]:
    """Scrape active codes from https://www.wosrewards.com/.

    Strategy:
    - Active cards appear before the first "EXPIRED" marker.
    - Codes are inside <h5 class="font-bold mb-2 ..."> elements; expired ones add
      the class "opacity-60". We only read <h5> elements in the active section
      and ignore any with non-alphanumeric text.
    """
    import requests

    url = "https://www.wosrewards.com/"
    headers = {"User-Agent": os.getenv("RSS_USER_AGENT", "wos-redeemer/1.0 (+html)")}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    html = r.text

    # Consider only the portion before the first "EXPIRED" marker.
    cut = html.find("EXPIRED")
    active_html = html if cut < 0 else html[:cut]

    # Extract codes from <h5> elements.
    # Keep text that is strictly alphanumeric (case-sensitive) and 4..24 chars.
    # This avoids unrelated words from scripts like DOCUMENTELEMENT, JSDELIVR, etc.
    h5_texts = re.findall(r"<h5[^>]*>(.*?)</h5>", active_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned: list[str] = []
    for t in h5_texts:
        # Drop any nested tags and trim
        t = re.sub(r"<[^>]+>", "", t).strip()
        if not t:
            continue
        if not re.fullmatch(r"[A-Za-z0-9]{4,24}", t):
            continue
        cleaned.append(t)
    # De-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in cleaned:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


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

def scrape_loop(interval_seconds: int = 300) -> None:
    while True:
        started = datetime.now(timezone.utc)
        try:
            # Only ingest active codes from wosrewards.com
            active_codes = _scrape_wosrewards_active()
            if active_codes:
                with SessionLocal() as db:
                    for code in active_codes:
                        exists = db.scalar(select(GiftCode).where(GiftCode.code == code))
                        if exists:
                            continue
                        gc = GiftCode(code=code, title=None, description=None, source_url="https://www.wosrewards.com/")
                        db.add(gc)
                        db.commit()
        except Exception:
            pass
        # Basic heartbeat marker (could be improved to DB status table)
        with open(_status_path(".rss_heartbeat"), "w") as f:
            f.write(started.isoformat())
        time.sleep(interval_seconds)


def eligible_count(db: Session) -> int:
    """Count eligible (user, code) pairs below cap and outside backoff.

    Uses a cross-count with NOT EXISTS filters to avoid Python-side cartesian loops.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.now(timezone.utc)
    # Treat any terminal outcome as ineligible.
    final_like = list(RedemptionStatus.final_statuses())
    stmt = (
        select(func.count())
        .select_from(User, GiftCode)
        .where(User.active == True, GiftCode.active == True)
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == User.id,
            Redemption.gift_code_id == GiftCode.id,
            Redemption.status.in_(final_like),
        )))
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == User.id,
            Redemption.gift_code_id == GiftCode.id,
            Redemption.attempt_count >= MAX_ATTEMPTS_PER_PAIR,
        )))
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == User.id,
            Redemption.gift_code_id == GiftCode.id,
            Redemption.last_attempt_at.is_not(None),
            Redemption.last_attempt_at > cutoff,
        )))
    )
    return int(db.scalar(stmt) or 0)


def _eligible_pairs(db: Session, limit_codes: int = 20, limit_users: int = 200) -> List[QueueItem]:
    """Return eligible (user, code) pairs ready to process now.

    Applies: active flags, success filter, retry backoff window, and max attempts per pair.
    Ordered by code first_seen_at asc, then user id asc.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.now(timezone.utc)

    # Get active codes ordered by first_seen_at
    codes = db.scalars(
        select(GiftCode)
        .where(GiftCode.active == True)
        .order_by(GiftCode.first_seen_at.asc())
        .limit(max(1, limit_codes))
    ).all()

    # Get active users ordered by id
    users = db.scalars(
        select(User)
        .where(User.active == True)
        .order_by(User.id.asc())
        .limit(max(1, limit_users))
    ).all()

    out: List[QueueItem] = []

    for code in codes:
        for user in users:
            # Check if redemption exists
            redemption = db.scalar(
                select(Redemption)
                .where(
                    Redemption.user_id == user.id,
                    Redemption.gift_code_id == code.id
                )
            )

            # Skip if redemption is in a final state
            if redemption and RedemptionStatus.is_final(redemption.status):
                continue

            # Skip if attempt count exceeded
            if redemption and redemption.attempt_count >= MAX_ATTEMPTS_PER_PAIR:
                continue

            # Skip if within backoff window
            if redemption and redemption.last_attempt_at:
                last = _as_utc(redemption.last_attempt_at)
                if last and last > cutoff:
                    continue

            out.append(QueueItem(
                user_id=user.id,
                fid=user.fid,
                name=user.name,
                gift_code_id=code.id,
                code=code.code
            ))

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
    api_key = None
    while True:
        attempts_made = 0
        successes = 0
        errors = 0
        try:
            api_key = api_key or (os.getenv(openrouter_api_key_env) or None)  # type: ignore[name-defined]
            if not api_key:
                # Still emit heartbeats/status so UI shows liveness without API key
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
                        "note": "no_openrouter_api_key",
                        "current": None,
                        "eligible": ec,
                    }
                    with open(_status_path(".worker_status"), "w") as f:
                        f.write(json.dumps(status))
                except Exception:
                    pass
                print(f"[worker] OpenRouter API key missing; sleeping {poll_seconds}s", flush=True)
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
                    attempt_notes: list[str] = []
                    last_err_code: Optional[int] = None
                    last_captcha: Optional[str] = None
                    stop_this_cycle = False

                    for outer_i in range(1, REDEEM_OUTER_RETRIES + 1):
                        # Ensure /player session (profile)
                        try:
                            prof = api.call_player(user.fid)
                            if prof.get("code") != 0:
                                raise RuntimeError(f"/player nonzero code: {prof}")
                            print(f"[worker] fid={user.fid} /player ok", flush=True)
                        except Exception as e:
                            note = f"outer#{outer_i} /player error: {e}"
                            attempt_notes.append(note)
                            print(f"[worker] {note}", flush=True)
                            _sleep_backoff(1.0)
                            continue

                        # Inner loop: retry CAPTCHA inline up to REDEEM_INNER_RETRIES
                        inner_i = 0
                        while inner_i < REDEEM_INNER_RETRIES:
                            inner_i += 1
                            # Always fetch captcha; backend requires it as of 2025-10-04.
                            try:
                                cap = api.call_captcha(user.fid)
                                data_url = cap.get("data", {}).get("img")
                                if not isinstance(data_url, str):
                                    raise RuntimeError("captcha response missing image data")
                                print(f"[worker] fid={user.fid} /captcha ok", flush=True)
                            except Exception as e:
                                note = f"outer#{outer_i} inner#{inner_i} /captcha error: {e}"
                                attempt_notes.append(note)
                                print(f"[worker] {note}", flush=True)
                                _sleep_backoff(1.0)
                                continue

                            # Solve via OpenRouter
                            try:
                                captcha = solve_captcha_via_openrouter(data_url, api_key)
                                last_captcha = captcha
                                print(f"[worker] fid={user.fid} openrouter captcha={captcha}", flush=True)
                            except CaptchaSolverError as e:
                                print(f"[worker] fid={user.fid} openrouter error: {e}", flush=True)
                                # Persist the CAPTCHA image with the exact attempted guess
                                try:
                                    out_path = save_failure_captcha(
                                        data_url,
                                        fid=user.fid,
                                        guess=(e.guess or "none"),
                                        reason="openrouter_error",
                                    )
                                    print(f"[worker] saved failed captcha to: {out_path}", flush=True)
                                except Exception:
                                    pass
                                attempt_notes.append(f"outer#{outer_i} inner#{inner_i} solver error: {e}")
                                _sleep_backoff(1.0)
                                continue
                            except Exception as e:
                                # Non-parsing errors (e.g., HTTP) — do not save image per requirements
                                note = f"outer#{outer_i} inner#{inner_i} solver http/unknown error: {e}"
                                print(f"[worker] {note}", flush=True)
                                attempt_notes.append(note)
                                _sleep_backoff(1.0)
                                continue

                            # Redeem with captcha
                            resp = api.call_gift_code(user.fid, code.code, captcha)
                            status_code = resp.get("code")
                            msg = resp.get("msg")
                            err_code = resp.get("err_code")
                            last_err_code = int(err_code) if isinstance(err_code, int) else None
                            print(f"[worker] fid={user.fid} /gift_code code={status_code} msg={msg} err={err_code}", flush=True)

                            msg_norm = (str(msg) if isinstance(msg, str) else "").strip().rstrip(".").upper()
                            # Outcome branches
                            if msg_norm in {"SUCCESS"} or status_code == 0:
                                redemption.status = RedemptionStatus.redeemed_new.value
                                redemption.captcha = last_captcha
                                redemption.result_msg = str(msg)
                                successes += 1
                                break  # exit inner
                            elif msg_norm in {"RECEIVED", "SAME TYPE EXCHANGE", "ALREADY RECEIVED"}:
                                redemption.status = RedemptionStatus.redeemed_already.value
                                redemption.captcha = last_captcha
                                redemption.result_msg = str(msg)
                                successes += 1
                                break  # exit inner
                            elif msg_norm in {"CDK NOT FOUND", "USED", "TIME ERROR"}:
                                # Globally invalid/expired/consumed code — deactivate & drain queue
                                try:
                                    code.active = False
                                    db.commit()
                                    worker_state.clear()
                                    _refill_queue(db)
                                    print(f"[worker] code={code.code} marked inactive due to '{msg_norm}'. queue reset+refill", flush=True)
                                except Exception:
                                    db.rollback()
                                if redemption.status not in (RedemptionStatus.redeemed_new.value, RedemptionStatus.redeemed_already.value):
                                    redemption.status = RedemptionStatus.failed.value
                                redemption.captcha = last_captcha
                                redemption.result_msg = str(msg)
                                errors += 1
                                stop_this_cycle = True
                                break  # exit inner
                            elif "RECHARGE" in msg_norm and "VIP" in msg_norm:
                                # User doesn't meet required VIP level for this code — fail this pair (do not deactivate code)
                                redemption.status = RedemptionStatus.failed.value
                                redemption.captcha = last_captcha
                                redemption.result_msg = str(msg)
                                errors += 1
                                print(f"[worker] fid={user.fid} code={code.code} failed due to VIP level requirement: {msg_norm}", flush=True)
                                break  # exit inner; no further retries for this attempt
                            elif msg_norm in {"NOT LOGIN", "TIMEOUT RETRY"}:
                                note = f"outer#{outer_i} inner#{inner_i} backend={msg_norm} -> retry outer"
                                print(f"[worker] {note}", flush=True)
                                attempt_notes.append(note)
                                # Break inner to retry outer
                                break
                            elif msg_norm == "CAPTCHA CHECK ERROR":
                                note = f"outer#{outer_i} inner#{inner_i} captcha wrong; will retry"
                                print(f"[worker] {note}", flush=True)
                                attempt_notes.append(note)
                                try:
                                    LOGGER.info(json.dumps({"event": "captcha_check_error", "fid": user.fid, "code": code.code}))
                                except Exception:
                                    pass
                                try:
                                    out_path = save_failure_captcha(data_url, fid=user.fid, guess=captcha, reason="captcha_check_error")
                                    print(f"[worker] saved failed captcha to: {out_path}", flush=True)
                                except Exception:
                                    pass
                                errors += 1
                                # Backoff: ATTEMPT_DELAY_S + jitter
                                _sleep_backoff(1.0)
                                continue
                            elif msg_norm == "CAPTCHA CHECK TOO FREQUENT":
                                try:
                                    LOGGER.info(json.dumps({"event": "captcha_too_frequent", "fid": user.fid, "code": code.code}))
                                except Exception:
                                    pass
                                attempt_notes.append(f"outer#{outer_i} inner#{inner_i} captcha too frequent; retrying")
                                # Backoff: 2 * ATTEMPT_DELAY_S + jitter
                                _sleep_backoff(2.0)
                                continue
                            else:
                                # Unknown message — record and continue inner retries
                                attempt_notes.append(f"outer#{outer_i} inner#{inner_i} unknown msg={msg_norm}")
                                errors += 1
                                _sleep_backoff(1.0)
                                continue

                        # End inner loop: if we exited due to success/failed, break outer as well
                        if RedemptionStatus.is_final(redemption.status):
                            break
                        # Otherwise, continue next outer iteration

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
    t1 = threading.Thread(target=scrape_loop, name="web-scraper", daemon=True)
    t1.start()
    t2 = threading.Thread(target=redemption_worker_loop, name="redeem-worker", daemon=True)
    t2.start()
