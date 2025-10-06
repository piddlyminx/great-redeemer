from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
import random
from typing import Optional, List
import os
import json

import feedparser  # type: ignore
from sqlalchemy import select, func, exists
from sqlalchemy.orm import Session

from .db import SessionLocal, GiftCode, User, Redemption, RedemptionAttempt, RedemptionStatus
from . import api
from .solver import solve_captcha_via_openrouter, CaptchaSolverError
from .utils import save_failure_captcha
from .queueing import worker_state, QueueItem


RSS_URL = "https://wosgiftcodes.com/rss.php"

# Throttling: number of redemption attempts per worker cycle (default 2)
MAX_ATTEMPTS_PER_CYCLE = int(os.getenv("REDEEM_MAX_ATTEMPTS_PER_CYCLE", "2"))
# Max tries allowed per (user, code) pair
MAX_ATTEMPTS_PER_PAIR = int(os.getenv("REDEEM_MAX_ATTEMPTS_PER_PAIR", "3"))
# Optional delay between completed attempts (base seconds, jittered +/- 0.5s)
ATTEMPT_DELAY_S = float(os.getenv("REDEEM_DELAY_S", "2"))
# Skip redemptions attempted within this many minutes
MIN_RETRY_MINUTES = int(os.getenv("REDEEM_MIN_RETRY_MINUTES", "15"))
REDEEM_POLL_SECONDS = int(os.getenv("REDEEM_POLL_SECONDS", "20"))

# Where to write small heartbeat/status files so the API can read them.
# Defaults to current working directory; set to a shared volume path like "/state" in compose.
STATUS_DIR = os.getenv("STATUS_DIR", "")


def _status_path(name: str) -> str:
    base = STATUS_DIR or "."
    return os.path.join(base, name)



def extract_codes(text: str) -> list[str]:
    # Simple heuristic: uppercase letters/digits 6-16 length, plus common WOS patterns
    return re.findall(r"\b[A-Z0-9]{6,16}\b", text.upper())


def rss_scraper_loop(interval_seconds: int = 300) -> None:
    while True:
        started = datetime.utcnow()
        try:
            feed = feedparser.parse(RSS_URL)
            for entry in feed.entries:
                title = entry.get("title", "") or ""
                summary = entry.get("summary", "") or ""
                link = entry.get("link", "") or ""
                found = set(extract_codes(title + "\n" + summary))
                if not found:
                    continue
                with SessionLocal() as db:
                    for code in found:
                        exists = db.scalar(select(GiftCode).where(GiftCode.code == code))
                        if exists:
                            continue
                        gc = GiftCode(code=code, title=title[:255] or None, description=summary or None, source_url=link or None)
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
    cutoff = datetime.utcnow() - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.utcnow()
    stmt = (
        select(func.count())
        .select_from(User, GiftCode)
        .where(User.active == True, GiftCode.active == True)
        .where(~exists(select(Redemption.id).where(
            Redemption.user_id == User.id,
            Redemption.gift_code_id == GiftCode.id,
            Redemption.status == RedemptionStatus.success.value,
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
    cutoff = datetime.utcnow() - timedelta(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.utcnow()
    codes = db.scalars(
        select(GiftCode).where(GiftCode.active == True).order_by(GiftCode.first_seen_at.asc()).limit(max(1, limit_codes))
    ).all()
    users = db.scalars(
        select(User).where(User.active == True).order_by(User.id.asc()).limit(max(1, limit_users))
    ).all()
    out: List[QueueItem] = []
    for code in codes:
        for user in users:
            red = db.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
            if red and red.status == RedemptionStatus.success.value:
                continue
            if red and red.attempt_count >= MAX_ATTEMPTS_PER_PAIR:
                continue
            if red and red.last_attempt_at and red.last_attempt_at > cutoff:
                continue
            out.append(QueueItem(user_id=user.id, fid=user.fid, name=user.name, gift_code_id=code.id, code=code.code))
    return out


def _refill_queue(db: Session, target_min_size: int = 50) -> int:
    """Top up the in-memory queue with eligible pairs up to a minimum size.

    Returns the number of items added.
    """
    if len(worker_state.queue) >= target_min_size:
        return 0
    pairs = _eligible_pairs(db)
    return worker_state.add_unique(pairs)


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
                        f.write(datetime.utcnow().isoformat())
                    status = {
                        "ts": datetime.utcnow().isoformat(),
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
                    if attempts_made >= max(1, MAX_ATTEMPTS_PER_CYCLE):
                        break
                    item = worker_state.pop()
                    if item is None:
                        break
                    user = db.get(User, item.user_id)
                    code = db.get(GiftCode, item.gift_code_id)
                    if not user or not code or not user.active or not code.active:
                        continue
                    redemption = db.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
                    if redemption and redemption.status == RedemptionStatus.success.value:
                        continue
                    if not redemption:
                        redemption = Redemption(user_id=user.id, gift_code_id=code.id)
                        db.add(redemption)
                        db.commit()
                        db.refresh(redemption)

                    # Skip if we've already tried max_attempts_per_pair
                    if redemption.attempt_count >= max_attempts_per_pair:
                        if redemption.status == RedemptionStatus.pending.value:
                            redemption.status = RedemptionStatus.failed.value
                            db.commit()
                        continue

                    # Respect backoff
                    if redemption.last_attempt_at and redemption.last_attempt_at > datetime.utcnow() - timedelta(minutes=MIN_RETRY_MINUTES):
                        continue

                    print(f"[worker] fid={user.fid} code={code.code} starting attempt #{redemption.attempt_count + 1}")
                    # Update lightweight current status for UI peek
                    try:
                        import json as _json
                        status_patch = {
                            "ts": datetime.utcnow().isoformat(),
                            "current": {"fid": user.fid, "user_id": user.id, "code": code.code, "gift_code_id": code.id},
                            "queue": [
                                {"fid": q.fid, "user_id": q.user_id, "name": q.name, "code": q.code, "gift_code_id": q.gift_code_id}
                                for q in worker_state.snapshot(10)
                            ],
                        }
                        try:
                            with open(".worker_status") as f:
                                cur = _json.loads(f.read())
                        except Exception:
                            cur = {}
                        cur.update(status_patch)
                        with open(".worker_status", "w") as f:
                            f.write(_json.dumps(cur))
                    except Exception:
                        pass

                    # Ensure /player session (profile) — log failures
                    try:
                        prof = api.call_player(user.fid)
                        if prof.get("code") != 0:
                            raise RuntimeError(f"/player nonzero code: {prof}")
                        print(f"[worker] fid={user.fid} /player ok", flush=True)
                    except Exception as e:
                        print(f"[worker] fid={user.fid} /player error: {e}", flush=True)
                        att = RedemptionAttempt(
                            redemption_id=redemption.id,
                            attempt_no=redemption.attempt_count + 1,
                            result_msg=f"player error: {e}",
                        )
                        db.add(att)
                        redemption.attempt_count += 1
                        redemption.last_attempt_at = datetime.utcnow()
                        db.commit()
                        errors += 1
                        # move on to next user/code
                        continue

                    # Always fetch captcha; backend requires it as of 2025-10-04.
                    try:
                        cap = api.call_captcha(user.fid)
                        data_url = cap.get("data", {}).get("img")
                        if not isinstance(data_url, str):
                            raise RuntimeError("captcha response missing image data")
                        print(f"[worker] fid={user.fid} /captcha ok", flush=True)
                    except Exception as e:
                        att = RedemptionAttempt(
                            redemption_id=redemption.id,
                            attempt_no=redemption.attempt_count + 1,
                            result_msg=f"captcha request error: {e}",
                        )
                        db.add(att)
                        redemption.attempt_count += 1
                        redemption.last_attempt_at = datetime.utcnow()
                        db.commit()
                        print(f"[worker] fid={user.fid} /captcha error: {e}", flush=True)
                        errors += 1
                        continue

                    try:
                        captcha = solve_captcha_via_openrouter(data_url, api_key)
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
                        att = RedemptionAttempt(
                            redemption_id=redemption.id,
                            attempt_no=redemption.attempt_count + 1,
                            result_msg=f"solver error: {e}",
                        )
                        db.add(att)
                        redemption.attempt_count += 1
                        redemption.last_attempt_at = datetime.utcnow()
                        db.commit()
                        errors += 1
                        continue
                    except Exception as e:
                        # Non-parsing errors (e.g., HTTP) — do not save image per requirements
                        print(f"[worker] fid={user.fid} openrouter HTTP/unknown error: {e}", flush=True)
                        att = RedemptionAttempt(
                            redemption_id=redemption.id,
                            attempt_no=redemption.attempt_count + 1,
                            result_msg=f"solver error: {e}",
                        )
                        db.add(att)
                        redemption.attempt_count += 1
                        redemption.last_attempt_at = datetime.utcnow()
                        db.commit()
                        errors += 1
                        continue

                    # redeem with captcha
                    resp = api.call_gift_code(user.fid, code.code, captcha)
                    status_code = resp.get("code")
                    msg = resp.get("msg")
                    err_code = resp.get("err_code")
                    print(f"[worker] fid={user.fid} /gift_code code={status_code} msg={msg} err={err_code}", flush=True)
                    att = RedemptionAttempt(
                        redemption_id=redemption.id,
                        attempt_no=redemption.attempt_count + 1,
                        captcha=captcha,
                        result_msg=json.dumps(resp)[:1000] if isinstance(resp, dict) else str(resp),  # type: ignore[name-defined]
                        err_code=int(err_code) if isinstance(err_code, int) else None,
                    )
                    db.add(att)
                    redemption.attempt_count += 1
                    redemption.captcha = captcha
                    redemption.result_msg = msg
                    msg_norm = (str(msg) if isinstance(msg, str) else "").strip().rstrip(".").upper()
                    if msg_norm in {"SUCCESS"} or status_code == 0:
                        redemption.status = RedemptionStatus.success.value
                        successes += 1
                    elif msg_norm in {"RECEIVED", "SAME TYPE EXCHANGE", "ALREADY RECEIVED"}:
                        redemption.status = RedemptionStatus.success.value
                        successes += 1
                    elif msg_norm == "NOT LOGIN":
                        try:
                            api.call_player(user.fid)
                            print(f"[worker] fid={user.fid} NOT LOGIN -> refreshed /player", flush=True)
                        except Exception as e:
                            print(f"[worker] fid={user.fid} NOT LOGIN -> /player failed: {e}", flush=True)
                            errors += 1
                    elif msg_norm == "CAPTCHA CHECK ERROR":
                        print(f"[worker] fid={user.fid} captcha wrong; will retry later", flush=True)
                        # Save the image with the incorrect guess
                        try:
                            out_path = save_failure_captcha(data_url, fid=user.fid, guess=captcha, reason="captcha_check_error")
                            print(f"[worker] saved failed captcha to: {out_path}", flush=True)
                        except Exception:
                            pass
                        errors += 1
                    redemption.err_code = err_code if isinstance(err_code, int) else None
                    redemption.last_attempt_at = datetime.utcnow()
                    db.commit()

                    attempts_made += 1
                    if ATTEMPT_DELAY_S > 0:
                        delay = max(0.0, ATTEMPT_DELAY_S + random.uniform(-0.5, 0.5))
                        time.sleep(delay)
        except Exception:
            pass

        # heartbeat file
        with open(_status_path(".worker_heartbeat"), "w") as f:
            f.write(datetime.utcnow().isoformat())

        # Write compact status for UI
        try:
            with SessionLocal() as db2:
                ec = eligible_count(db2)
            status = {
                "ts": datetime.utcnow().isoformat(),
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
        print(f"[worker] cycle summary: attempts={attempts_made} success={successes} errors={errors}; sleeping {poll_seconds}s", flush=True)
        time.sleep(poll_seconds)


def start_background_threads() -> None:
    t1 = threading.Thread(target=rss_scraper_loop, name="rss-scraper", daemon=True)
    t1.start()
    t2 = threading.Thread(target=redemption_worker_loop, name="redeem-worker", daemon=True)
    t2.start()
