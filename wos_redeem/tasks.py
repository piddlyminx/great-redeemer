from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
import random
from typing import Optional
import os
import json

import feedparser  # type: ignore
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import SessionLocal, GiftCode, User, Redemption, RedemptionAttempt, RedemptionStatus
from . import api
from .solver import solve_captcha_via_openrouter, CaptchaSolverError
from .utils import save_failure_captcha


RSS_URL = "https://wosgiftcodes.com/rss.php"

# Throttling: number of redemption attempts per worker cycle (default 2)
MAX_ATTEMPTS_PER_CYCLE = int(os.getenv("REDEEM_MAX_ATTEMPTS_PER_CYCLE", "2"))
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


def redemption_worker_loop(openrouter_api_key_env: str = "OPENROUTER_API_KEY", max_attempts_per_pair: int = 3, poll_seconds: int = None) -> None:
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
                    with open(_status_path(".worker_heartbeat"), "w") as f:
                        f.write(datetime.utcnow().isoformat())
                    status = {
                        "ts": datetime.utcnow().isoformat(),
                        "attempts": attempts_made,
                        "successes": successes,
                        "errors": errors,
                        "sleep": poll_seconds,
                        "note": "no_openrouter_api_key",
                    }
                    with open(_status_path(".worker_status"), "w") as f:
                        f.write(json.dumps(status))
                except Exception:
                    pass
                print(f"[worker] OpenRouter API key missing; sleeping {poll_seconds}s", flush=True)
                time.sleep(poll_seconds)
                continue
            with SessionLocal() as db:
                # One-time reconciliation each cycle: mark any redemptions with a RECEIVED msg as success
                try:
                    cutoff = datetime.utcnow() - timedelta(minutes=MIN_RETRY_MINUTES)
                    # no-op variable to keep linter happy; we operate via ORM for portability
                    reds = db.scalars(
                        select(Redemption)
                        .where(Redemption.status != RedemptionStatus.success.value)
                    ).all()
                    for r in reds:
                        # find last attempt
                        if r.attempts:
                            last = r.attempts[-1]
                            if last and last.result_msg and '"msg": "RECEIVED' in last.result_msg:
                                r.status = RedemptionStatus.success.value
                    db.commit()
                except Exception:
                    pass
                # Find oldest active code with users needing redemption
                # Strategy: iterate codes by first_seen_at; for each, find active users without redemption record or with pending
                codes = db.scalars(select(GiftCode).where(GiftCode.active == True).order_by(GiftCode.first_seen_at.asc()).limit(10)).all()
                attempts_made = 0
                successes = 0
                errors = 0
                stop_cycle = False
                for code in codes:
                    # candidate users
                    users = db.scalars(select(User).where(User.active == True)).all()
                    for user in users:
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

                        # Respect backoff: skip if we attempted within the last MIN_RETRY_MINUTES
                        if redemption.last_attempt_at and redemption.last_attempt_at > datetime.utcnow() - timedelta(minutes=MIN_RETRY_MINUTES):
                            continue

                        print(f"[worker] fid={user.fid} code={code.code} starting attempt #{redemption.attempt_count + 1}")
                        # Update lightweight current status for UI peek
                        try:
                            import json as _json
                            status_patch = {
                                "ts": datetime.utcnow().isoformat(),
                                "current": {"fid": user.fid, "user_id": user.id, "code": code.code, "gift_code_id": code.id},
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
                        if attempts_made >= max(1, MAX_ATTEMPTS_PER_CYCLE):
                            stop_cycle = True
                            break
                    if stop_cycle:
                        break
        except Exception:
            pass

        # heartbeat file
        with open(_status_path(".worker_heartbeat"), "w") as f:
            f.write(datetime.utcnow().isoformat())

        # Write compact status for UI
        try:
            status = {
                "ts": datetime.utcnow().isoformat(),
                "attempts": attempts_made,
                "successes": successes,
                "errors": errors,
                "sleep": poll_seconds,
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
