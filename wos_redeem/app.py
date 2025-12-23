from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta as _td
import asyncio
import re
import os
from typing import Optional

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
except Exception:  # pragma: no cover
    ProxyHeadersMiddleware = None  # type: ignore
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
# from fastapi.templating import Jinja2Templates  # no longer used; SPA replaced HTML pages
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import SessionLocal, Alliance, User, GiftCode, Redemption, RedemptionAttempt, WebAccount, WebRole
from .auth import ensure_bootstrap_admin, verify_password, hash_password
from .cf_access import get_verifier
from .tasks import start_background_threads, MIN_RETRY_MINUTES, MAX_ATTEMPTS_PER_PAIR


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _normalize_base_path(p: str) -> str:
    p = (p or "").strip()
    if p in ("", "/"):
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")

BASE_PATH = _normalize_base_path(os.getenv("BASE_PATH", ""))
STATUS_DIR = os.getenv("STATUS_DIR", "")

app = FastAPI(title="WOS Redeemer Admin", root_path=BASE_PATH)

# Honor X-Forwarded-* headers from Traefik for scheme/host
if ProxyHeadersMiddleware is not None:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Honor X-Forwarded-Prefix for URL generation by setting scope.root_path dynamically
class XForwardedPrefixMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            prefix = headers.get("x-forwarded-prefix")
            # Avoid interfering with StaticFiles resolution: do not set root_path for asset requests.
            path = scope.get("path") or ""
            if prefix and not (path.startswith("/assets/") or path == "/assets"):
                scope = dict(scope)
                scope["root_path"] = prefix.rstrip("/") or "/"
        await self.app(scope, receive, send)

app.add_middleware(XForwardedPrefixMiddleware)

# CORS for local SPA dev (vite @ http://localhost:5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# templates removed


@app.on_event("startup")
def on_startup():
    # one-time bootstrap admin if configured
    with SessionLocal() as db:
        ensure_bootstrap_admin(db)
    # Start workers inside the API container only if explicitly enabled
    if os.getenv("START_WORKERS", "0") == "1":
        start_background_threads()

# Mount SPA assets at a single canonical path: /assets
try:
    app.mount("/assets", StaticFiles(directory="static/ui/assets", html=False), name="spa_assets")
except Exception:
    pass

# (SPA index + catch-all moved to bottom so API/static routes take precedence)


def current_account(request: Request, db: Session = Depends(get_db)) -> WebAccount | None:
    # Prefer Cloudflare Access
    cf_jwt = None
    for k, v in request.headers.items():
        if k.lower() == "cf-access-jwt-assertion":
            cf_jwt = v
            break
    email = request.headers.get("Cf-Access-Authenticated-User-Email") or request.headers.get("cf-access-authenticated-user-email")
    if cf_jwt:
        verifier = get_verifier()
        if verifier is not None:
            try:
                claims = verifier.verify(cf_jwt)
                email = email or claims.get("email") or claims.get("identity")
            except Exception:
                email = None
        if email:
            acct = db.scalar(select(WebAccount).where(WebAccount.username == email))
            if not acct:
                # Auto-provision as inactive manager
                acct = WebAccount(username=email, password_hash=hash_password(os.urandom(8).hex()), role=WebRole.manager.value, active=False)
                db.add(acct)
                db.commit()
            return acct if acct.active else None
    return None


def require_login(acct: WebAccount | None) -> WebAccount:
    if not acct:
        raise HTTPException(status_code=401)
    return acct


def require_admin(acct: WebAccount | None) -> WebAccount:
    acct = require_login(acct)
    if acct.role != WebRole.admin.value:
        raise HTTPException(403)
    return acct


# Auth toggles
# - REQUIRE_AUTH_FOR_READ: gate read-only views (keep for future use)
# - DISABLE_AUTH_ALL: disable all auth checks (read + write) for local/dev
READ_AUTH_REQUIRED = os.getenv("REQUIRE_AUTH_FOR_READ", "0") not in ("0", "false", "False", "")
DISABLE_AUTH_ALL = os.getenv("DISABLE_AUTH_ALL", "0") in ("1", "true", "True")
ALLOW_PUBLIC_MONITOR = os.getenv("ALLOW_PUBLIC_MONITOR", "0") in ("1", "true", "True")


# Deprecated server-rendered admin pages removed; replaced by SPA


# HTML alliances routes removed


# HTML users routes removed


# HTML codes route removed


# HTML user detail route removed


# HTML login route removed (Cloudflare Access used)


# Removed in-app OAuth and cookie sessions; rely on Cloudflare Access instead


# HTML monitor route removed; use /api/attempts


@app.get("/_debug/access")
def debug_access(request: Request):
    # DB-free debug endpoint; never fails due to DB/auth. No secrets are returned.
    headers = {k.lower(): v for k, v in request.headers.items()}
    cf_email = headers.get("cf-access-authenticated-user-email")
    jwt_raw = headers.get("cf-access-jwt-assertion")
    jwt_info = {"present": bool(jwt_raw), "len": len(jwt_raw) if jwt_raw else 0}
    info = {
        "root_path": request.scope.get("root_path"),
        "url_path": request.url.path,
        "host": headers.get("host"),
        "x_forwarded_proto": headers.get("x-forwarded-proto"),
        "x_forwarded_prefix": headers.get("x-forwarded-prefix"),
        "cf_access_user_email": cf_email,
        "cf_access_jwt": jwt_info,
        "flags": {
            "READ_AUTH_REQUIRED": READ_AUTH_REQUIRED,
            "DISABLE_AUTH_ALL": DISABLE_AUTH_ALL,
            "BASE_PATH": BASE_PATH,
        },
    }
    return JSONResponse(info)


# ------------------ JSON API for SPA ------------------
def _require(cond: bool):
    if not cond:
        raise HTTPException(401)


@app.get("/api/summary")
def api_summary(db: Session = Depends(get_db)):
    users = db.scalar(select(func.count(User.id))) or 0
    codes = db.scalar(select(func.count(GiftCode.id))) or 0
    success = db.scalar(select(func.count(Redemption.id)).where(Redemption.status.in_(["redeemed_new", "redeemed_already", "success"]))) or 0
    failed = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "failed")) or 0
    # Prefer worker-computed eligible backlog if available; fallback to SQL estimate
    fallback_pending = None
    try:
        from sqlalchemy import exists
        from .tasks import MAX_ATTEMPTS_PER_PAIR, MIN_RETRY_MINUTES as _MIN
        cutoff = datetime.now(timezone.utc)
        if _MIN:
            cutoff = datetime.now(timezone.utc) - _td(minutes=_MIN)
        s = (
            select(func.count())
            .select_from(User, GiftCode)
            .where(User.active == True, GiftCode.active == True)
            .where(~exists(select(Redemption.id).where(
                Redemption.user_id == User.id,
                Redemption.gift_code_id == GiftCode.id,
                Redemption.status.in_(["redeemed_new", "redeemed_already", "success"]),
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
        fallback_pending = int(db.scalar(s) or 0)
    except Exception:
        # Last resort: traditional pending redemptions
        fallback_pending = int(db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "pending")) or 0)
    def read_file(p):
        try:
            base = STATUS_DIR or "."
            full = os.path.join(base, p)
            with open(full) as f:
                return f.read().strip()
        except Exception:
            return None
    rss_hb = read_file(".rss_heartbeat")
    worker_hb = read_file(".worker_heartbeat")
    try:
        import json
        base = STATUS_DIR or "."
        with open(os.path.join(base, ".worker_status")) as f:
            worker_status = json.loads(f.read())
    except Exception:
        worker_status = None
    pending = worker_status.get("eligible") if isinstance(worker_status, dict) and worker_status.get("eligible") is not None else fallback_pending
    return {
        "users": users,
        "codes": codes,
        "success": success,
        "failed": failed,
        "pending": pending,
        "rss_hb": rss_hb,
        "worker_hb": worker_hb,
        "worker_status": worker_status,
    }


@app.get("/api/alliances")
def api_alliances(db: Session = Depends(get_db)):
    rows = db.scalars(select(Alliance).order_by(Alliance.name)).all()
    out = []
    for a in rows:
        mgrs = []
        for m in (a.managers or []):
            mgrs.append({
                "id": m.id,
                "username": m.username,
                "rank": m.alliance_rank or "R4",
                "active": m.active,
            })
        out.append({
            "id": a.id,
            "name": a.name,
            "tag": a.tag,
            "quota": a.quota,
            "members": len(a.users or []),
            "managers": mgrs,
        })
    return out


@app.post("/api/alliances")
def api_create_alliance(name: str = Form(...), tag: str = Form(...), quota: int = Form(0), db: Session = Depends(get_db)):
    if not DISABLE_AUTH_ALL:
        # simple check: placeholder for future auth
        _require(True)
    # Validate and preserve user-provided case for the 3-letter alliance tag
    name_val = (name or "").strip()
    if not name_val:
        raise HTTPException(status_code=400, detail="name is required")
    tag_val = (tag or "").strip()
    if not re.fullmatch(r"[A-Za-z]{3}", tag_val):
        raise HTTPException(status_code=400, detail="tag must be exactly 3 letters")
    a = Alliance(name=name_val, tag=tag_val, quota=max(0, int(quota)))
    db.add(a)
    db.commit()
    return {"ok": True, "id": a.id}


@app.post("/api/alliances/{alliance_id}")
def api_update_alliance(
    alliance_id: int,
    name: Optional[str] = Form(None),
    tag: Optional[str] = Form(None),
    quota: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    if not DISABLE_AUTH_ALL:
        _require(True)
    a = db.get(Alliance, alliance_id)
    if not a:
        raise HTTPException(404)
    if name is not None:
        nval = (name or "").strip()
        if not nval:
            raise HTTPException(status_code=400, detail="name must be non-empty")
        a.name = nval
    if tag is not None:
        tval = tag.strip()
        if not re.fullmatch(r"[A-Za-z]{3}", tval):
            raise HTTPException(status_code=400, detail="tag must be exactly 3 letters")
        # Preserve case as requested
        a.tag = tval
    if quota is not None:
        try:
            a.quota = max(0, int(quota))
        except Exception:
            raise HTTPException(status_code=400, detail="invalid quota")
    db.commit()
    return {"ok": True}


@app.get("/api/worker_peek")
def api_worker_peek(limit: int = 5, db: Session = Depends(get_db)):
    """Return a small snapshot of the worker state and upcoming work.

    - current: read from .worker_status file if present
    - recent: last few attempts with fid/code/status
    - upcoming: naive preview of next user+code pairs based on current DB state
    """
    # current and queue preview from status file (single source of truth)
    import json as _json
    current = None
    queue_preview = []
    try:
        with open(os.path.join(STATUS_DIR or ".", ".worker_status")) as f:
            w = _json.loads(f.read())
            current = w.get("current")
            queue_preview = w.get("queue") or []
    except Exception:
        current = None
        queue_preview = []
    # Enrich current with user name if possible
    try:
        if current and current.get("user_id"):
            u = db.get(User, int(current["user_id"]))
            if u:
                current["name"] = u.name
        elif current and current.get("fid"):
            u = db.scalar(select(User).where(User.fid == int(current["fid"])) )
            if u:
                current["name"] = u.name
    except Exception:
        pass

    # recent attempts joined with user fid and code, de-duplicated by (fid, code)
    recents = []
    seen_pairs = set()
    q = (
        db.query(RedemptionAttempt, Redemption, User, GiftCode)
        .join(Redemption, RedemptionAttempt.redemption_id == Redemption.id)
        .join(User, Redemption.user_id == User.id)
        .join(GiftCode, Redemption.gift_code_id == GiftCode.id)
        .order_by(RedemptionAttempt.created_at.desc())
        .limit(50)
    )
    for att, red, user, code in q.all():
        pair = (user.fid, code.code)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        ok = (red.status in ("redeemed_new", "redeemed_already"))
        recents.append({
            "id": att.id,
            "ts": att.created_at.isoformat() if att.created_at else None,
            "fid": user.fid,
            "name": user.name,
            "code": code.code,
            # Treat as error only if the overall redemption isn't a redeemed status
            "err": 0 if ok else 1,
            "msg": att.result_msg[:120] if att.result_msg else None,
        })
    recents = recents[: max(1, min(10, limit))]

    # Build a tiny queue preview ONLY from the worker queue snapshot
    # Do not issue DB lookups here; the worker is the single source of truth.
    upcoming = []
    for q in queue_preview[:limit]:
        upcoming.append({
            "fid": q.get("fid"),
            "user_id": q.get("user_id"),
            "name": q.get("name"),
            "code": q.get("code"),
            "gift_code_id": q.get("gift_code_id"),
        })

    return {"current": current, "recent": recents, "upcoming": upcoming[:limit]}


@app.get("/api/users")
def api_users(alliance_id: Optional[int] = None, q: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(User)
    if alliance_id:
        stmt = stmt.where(User.alliance_id == alliance_id)
    if q:
        q = q.strip()
        if q:
            try:
                fid = int(q)
                stmt = stmt.where(User.fid == fid)
            except ValueError:
                # partial match on name, case-insensitive
                from sqlalchemy import or_
                stmt = stmt.where(User.name.ilike(f"%{q}%"))
    rows = db.scalars(stmt.order_by(User.created_at.desc())).all()
    out = []
    for u in rows:
        out.append({
            "id": u.id,
            "fid": u.fid,
            "name": u.name,
            "alliance": (u.alliance.name if u.alliance else None),
            "tag": (u.alliance.tag if u.alliance else None),
            "active": u.active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    return out


@app.post("/api/users")
def api_create_user(
    fid: int = Form(...),
    name: Optional[str] = Form(None),
    alliance_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    if not DISABLE_AUTH_ALL:
        _require(True)
    # Validate alliance is provided and exists (business rule; not enforced at DB level)
    if not alliance_id:
        raise HTTPException(status_code=400, detail="alliance_id is required")
    if db.get(Alliance, int(alliance_id)) is None:
        raise HTTPException(status_code=400, detail="alliance not found")
    if db.scalar(select(User).where(User.fid == fid)):
        return {"ok": True, "existing": True}
    u = User(fid=fid, name=name or None, alliance_id=int(alliance_id))
    db.add(u)
    db.commit()
    return {"ok": True, "id": u.id}


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, db: Session = Depends(get_db)):
    """Delete a user and cascade-remove related rows.

    Hard delete is intentional so the User.fid unique constraint is freed for
    re-adding the same player later. Redemptions and attempts cascade via FK.
    """
    if not DISABLE_AUTH_ALL:
        _require(True)
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    db.delete(u)
    db.commit()
    return {"ok": True}

@app.get("/api/codes")
def api_codes(db: Session = Depends(get_db)):
    # Fetch recent codes (limit to keep aggregation bounded)
    codes = db.scalars(select(GiftCode).order_by(GiftCode.first_seen_at.desc()).limit(200)).all()
    if not codes:
        return []

    code_ids = [c.id for c in codes]
    redeemed_statuses = ["redeemed_new", "redeemed_already", "success"]

    # Aggregate counts per code in a few grouped queries
    rows_redeemed = db.execute(
        select(Redemption.gift_code_id, func.count(Redemption.id))
        .where(Redemption.gift_code_id.in_(code_ids), Redemption.status.in_(redeemed_statuses))
        .group_by(Redemption.gift_code_id)
    ).all()
    redeemed_map = {cid: int(cnt) for cid, cnt in rows_redeemed}

    rows_failed = db.execute(
        select(Redemption.gift_code_id, func.count(Redemption.id))
        .where(Redemption.gift_code_id.in_(code_ids), Redemption.status == "failed")
        .group_by(Redemption.gift_code_id)
    ).all()
    failed_map = {cid: int(cnt) for cid, cnt in rows_failed}

    # Distinct finished users (redeemed or failed) per code for pending computation on active codes
    rows_finished = db.execute(
        select(Redemption.gift_code_id, func.count(func.distinct(Redemption.user_id)))
        .where(
            Redemption.gift_code_id.in_(code_ids),
            Redemption.status.in_(redeemed_statuses + ["failed"]),
        )
        .group_by(Redemption.gift_code_id)
    ).all()
    finished_users_map = {cid: int(cnt) for cid, cnt in rows_finished}

    # Pending rows for inactive codes only (for active codes we compute below)
    rows_pending = db.execute(
        select(Redemption.gift_code_id, func.count(Redemption.id))
        .where(Redemption.gift_code_id.in_(code_ids), Redemption.status == "pending")
        .group_by(Redemption.gift_code_id)
    ).all()
    pending_rows_map = {cid: int(cnt) for cid, cnt in rows_pending}

    total_active_users = int(db.scalar(select(func.count(User.id)).where(User.active.is_(True))) or 0)

    out = []
    for c in codes:
        redeemed = redeemed_map.get(c.id, 0)
        failed = failed_map.get(c.id, 0)
        if c.active:
            finished = finished_users_map.get(c.id, 0)
            pending = max(0, total_active_users - finished)
        else:
            pending = pending_rows_map.get(c.id, 0)
        out.append(
            {
                "id": c.id,
                "code": c.code,
                "active": c.active,
                "source_created_at": c.source_created_at.isoformat() if c.source_created_at else None,
                "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
                "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                "redeemed": redeemed,
                "failed": failed,
                "pending": pending,
            }
        )
    return out


@app.get("/api/codes/{code}/detail")
def api_code_detail(code: str, db: Session = Depends(get_db)):
    gc = db.scalar(
        select(GiftCode)
        .where(GiftCode.code == code)
        .order_by(
            GiftCode.active.desc(),
            GiftCode.source_created_at.desc(),
            GiftCode.first_seen_at.desc(),
            GiftCode.id.desc(),
        )
    )
    if not gc:
        raise HTTPException(404)

    redeemed_statuses = ["redeemed_new", "redeemed_already", "success"]

    redeemed = int(
        db.scalar(
            select(func.count(Redemption.id)).where(
                Redemption.gift_code_id == gc.id,
                Redemption.status.in_(redeemed_statuses),
            )
        )
        or 0
    )

    failed = int(
        db.scalar(
            select(func.count(Redemption.id)).where(
                Redemption.gift_code_id == gc.id, Redemption.status == "failed"
            )
        )
        or 0
    )

    # For active codes, "pending" should include any active user who does not have
    # a redeemed or failed status for this code (even if no Redemption row exists).
    if gc.active:
        total_active_users = int(db.scalar(select(func.count(User.id)).where(User.active.is_(True))) or 0)
        finished_users = int(
            db.scalar(
                select(func.count(func.distinct(Redemption.user_id))).where(
                    Redemption.gift_code_id == gc.id,
                    Redemption.status.in_(redeemed_statuses + ["failed"]),
                )
            )
            or 0
        )
        pending = max(0, total_active_users - finished_users)
    else:
        pending = int(
            db.scalar(
                select(func.count(Redemption.id)).where(
                    Redemption.gift_code_id == gc.id, Redemption.status == "pending"
                )
            )
            or 0
        )

    # Build per-user rows
    rows: list[dict] = []
    if gc.active:
        # Show all active users; default status is "pending" if no Redemption row.
        from sqlalchemy import and_  # local import to avoid top clutter
        q = (
            select(User, Redemption)
            .outerjoin(
                Redemption,
                and_(Redemption.user_id == User.id, Redemption.gift_code_id == gc.id),
            )
            .where(User.active.is_(True))
            .order_by(User.created_at.desc())
        )
        for u, r in db.execute(q).all():
            last_dt = None
            if r is not None:
                last_dt = r.last_attempt_at or r.updated_at or r.created_at
            rows.append(
                {
                    "user_id": u.id,
                    "fid": u.fid,
                    "name": u.name,
                    "status": (r.status if r is not None and r.status else "pending"),
                    "attempt_count": (r.attempt_count if r is not None else 0),
                    "last_at": (last_dt.isoformat() if last_dt else None),
                }
            )
    else:
        # For inactive codes, list existing Redemption rows (typically historical results)
        q = (
            select(Redemption, User)
            .join(User, Redemption.user_id == User.id)
            .where(Redemption.gift_code_id == gc.id)
            .order_by(Redemption.updated_at.desc())
        )
        for r, u in db.execute(q).all():
            last_dt = r.last_attempt_at or r.updated_at or r.created_at
            rows.append(
                {
                    "user_id": u.id,
                    "fid": u.fid,
                    "name": u.name,
                    "status": r.status,
                    "attempt_count": r.attempt_count,
                    "last_at": last_dt.isoformat() if last_dt else None,
                }
            )

    return {
        "id": gc.id,
        "code": gc.code,
        "active": gc.active,
        "source_created_at": gc.source_created_at.isoformat() if gc.source_created_at else None,
        "first_seen_at": gc.first_seen_at.isoformat() if gc.first_seen_at else None,
        "expires_at": gc.expires_at.isoformat() if gc.expires_at else None,
        "summary": {"redeemed": redeemed, "failed": failed, "pending": pending},
        "users": rows,
    }


@app.get("/api/users/{user_id}/redemptions")
def api_user_redemptions(user_id: int, db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404)
    reds = db.scalars(select(Redemption).where(Redemption.user_id == user_id)).all()
    out = []
    for r in reds:
        out.append({
            "id": r.id,
            "code": r.gift_code.code,
            "status": r.status,
            "attempt_count": r.attempt_count,
            "last_attempt_at": r.last_attempt_at.isoformat() if r.last_attempt_at else None,
            "err_code": r.err_code,
        })
    return out


@app.post("/api/managers")
def api_create_manager(username: str = Form(...), password: str = Form(...), alliance_id: int = Form(...), rank: str = Form("R4"), db: Session = Depends(get_db), acct: WebAccount | None = Depends(current_account)):
    if not DISABLE_AUTH_ALL:
        require_admin(acct)
    if db.scalar(select(WebAccount).where(WebAccount.username == username)):
        return {"ok": True, "existing": True}
    wa = WebAccount(username=username, password_hash=hash_password(password), role=WebRole.manager.value, alliance_id=alliance_id, alliance_rank=rank)
    db.add(wa)
    db.commit()
    return {"ok": True, "id": wa.id}

@app.get("/api/attempts")
def api_attempts(limit: int = 50, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    attempts = db.scalars(select(RedemptionAttempt).order_by(RedemptionAttempt.created_at.desc()).limit(limit)).all()
    out = []
    for a in attempts:
        out.append({
            "id": a.id,
            "created_at": a.created_at.isoformat(),
            "user_fid": a.redemption.user.fid if a.redemption and a.redemption.user else None,
            "code": a.redemption.gift_code.code if a.redemption and a.redemption.gift_code else None,
            "captcha": a.captcha,
            "err_code": a.err_code,
            "result_msg": a.result_msg,
        })
    return out


@app.get("/api/worker_events")
async def api_worker_events(request: Request):
    """SSE stream of {summary, peek} updates.

    - Emits "peek" (current/upcoming + recent) immediately when the worker's
      status file changes, detected via a fast poll (SSE_POLL_MS; default 250ms).
    - Emits "summary" (aggregate counters) at a lower cadence
      (SSE_SUMMARY_INTERVAL_S; default 2s).
    - Falls back to polling on the client if SSE is unavailable.
    """

    async def event_gen():
        import json as _json
        import os as _os
        import time as _time

        status_path = _os.path.join(STATUS_DIR or ".", ".worker_status")
        # Tunables (env): fast file poll, slower summary cadence, and recents rate limit
        try:
            poll_ms = int(_os.getenv("SSE_POLL_MS", "250"))
        except Exception:
            poll_ms = 250
        poll_s = max(0.05, min(1.0, poll_ms / 1000.0))
        try:
            summary_interval_s = float(_os.getenv("SSE_SUMMARY_INTERVAL_S", "2"))
        except Exception:
            summary_interval_s = 2.0
        try:
            recents_interval_s = float(_os.getenv("SSE_RECENTS_INTERVAL_S", "1"))
        except Exception:
            recents_interval_s = 1.0

        last_sig = None  # (mtime_ns, size)
        last_summary_ts = 0.0
        last_recents_ts = 0.0
        cached_status = None

        # On connect, push at least one summary quickly
        first_tick = True

        while True:
            if await request.is_disconnected():
                break
            try:
                payload: dict = {}

                # Detect status file changes cheaply (no JSON parse unless changed)
                try:
                    st = _os.stat(status_path)
                    sig = (st.st_mtime_ns, st.st_size)
                except Exception:
                    sig = None

                if sig and sig != last_sig:
                    last_sig = sig
                    try:
                        with open(status_path) as f:
                            cached_status = _json.loads(f.read())
                    except Exception:
                        cached_status = None

                    # Build "peek" block
                    cur = None
                    queue_preview = []
                    if isinstance(cached_status, dict):
                        cur = cached_status.get("current")
                        queue_preview = cached_status.get("queue") or []

                    # Enrich current with user name (best effort)
                    try:
                        with SessionLocal() as db:
                            if cur and cur.get("user_id"):
                                u = db.get(User, int(cur["user_id"]))
                                if u:
                                    cur["name"] = u.name
                            elif cur and cur.get("fid"):
                                u = db.scalar(select(User).where(User.fid == int(cur["fid"])) )
                                if u:
                                    cur["name"] = u.name
                    except Exception:
                        pass

                    # Recent attempts (rate-limited to recents_interval_s)
                    recents = None
                    now = _time.time()
                    if (now - last_recents_ts) >= recents_interval_s or first_tick:
                        last_recents_ts = now
                        try:
                            with SessionLocal() as db:
                                q = (
                                    db.query(RedemptionAttempt, Redemption, User, GiftCode)
                                    .join(Redemption, RedemptionAttempt.redemption_id == Redemption.id)
                                    .join(User, Redemption.user_id == User.id)
                                    .join(GiftCode, Redemption.gift_code_id == GiftCode.id)
                                    .order_by(RedemptionAttempt.created_at.desc())
                                    .limit(100)
                                )
                                _recents = []
                                _seen = set()
                                for att, red, user, code in q.all():
                                    pair = (user.fid, code.code)
                                    if pair in _seen:
                                        continue
                                    _seen.add(pair)
                                    ok = (red.status in ("redeemed_new", "redeemed_already"))
                                    _recents.append({
                                        "id": att.id,
                                        "ts": att.created_at.isoformat() if att.created_at else None,
                                        "fid": user.fid,
                                        "name": user.name,
                                        "code": code.code,
                                        "err": 0 if ok else 1,
                                        "msg": att.result_msg[:120] if att.result_msg else None,
                                    })
                                recents = _recents[:5]
                        except Exception:
                            recents = None

                    upcoming = []
                    for q in (queue_preview or [])[:5]:
                        upcoming.append({
                            "fid": q.get("fid"),
                            "user_id": q.get("user_id"),
                            "name": q.get("name"),
                            "code": q.get("code"),
                            "gift_code_id": q.get("gift_code_id"),
                        })

                    payload["peek"] = {"current": cur, "recent": recents, "upcoming": upcoming[:5]}

                # Build "summary" block on cadence
                now = _time.time()
                if first_tick or (now - last_summary_ts) >= summary_interval_s:
                    last_summary_ts = now
                    try:
                        with SessionLocal() as db:
                            users = db.scalar(select(func.count(User.id))) or 0
                            codes = db.scalar(select(func.count(GiftCode.id))) or 0
                            success = db.scalar(select(func.count(Redemption.id)).where(Redemption.status.in_(["redeemed_new", "redeemed_already", "success"]))) or 0
                            failed = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "failed")) or 0
                            # Prefer worker-computed eligible backlog from cached status; fallback to SQL estimate
                            try:
                                pending = int((cached_status or {}).get("eligible")) if isinstance(cached_status, dict) and (cached_status or {}).get("eligible") is not None else None
                            except Exception:
                                pending = None
                            if pending is None:
                                try:
                                    from sqlalchemy import exists
                                    from .tasks import MAX_ATTEMPTS_PER_PAIR
                                    cutoff = datetime.now(timezone.utc) - _td(minutes=MIN_RETRY_MINUTES) if MIN_RETRY_MINUTES else datetime.now(timezone.utc)
                                    s = (
                                        select(func.count())
                                        .select_from(User, GiftCode)
                                        .where(User.active == True, GiftCode.active == True)
                                        .where(~exists(select(Redemption.id).where(
                                            Redemption.user_id == User.id,
                                            Redemption.gift_code_id == GiftCode.id,
                                            Redemption.status.in_(["redeemed_new", "redeemed_already", "success"]),
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
                                    with SessionLocal() as db2:
                                        pending = int(db2.scalar(s) or 0)
                                except Exception:
                                    pending = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "pending")) or 0

                        def _read(name: str):
                            try:
                                base = STATUS_DIR or "."
                                with open(os.path.join(base, name)) as f:
                                    return f.read().strip()
                            except Exception:
                                return None
                        rss_hb = _read(".rss_heartbeat")
                        worker_hb = _read(".worker_heartbeat")

                        payload["summary"] = {
                            "users": users,
                            "codes": codes,
                            "success": success,
                            "failed": failed,
                            "pending": pending,
                            "rss_hb": rss_hb,
                            "worker_hb": worker_hb,
                            "worker_status": cached_status,
                        }
                    except Exception:
                        # ignore summary errors; client will fall back to polling
                        pass

                if payload:
                    yield f"data: {_json.dumps(payload)}\n\n"
                first_tick = False
            except Exception:
                # Keep the stream alive on transient errors
                yield "data: {}\n\n"
            await asyncio.sleep(poll_s)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)

# ---------------- SPA fallback (register last) ----------------
def _is_spa_path_final(path: str) -> bool:
    return not (
        path.startswith("api/") or path.startswith("assets/") or path.startswith("_debug/") or path == "openapi.json"
    )

def _compute_base_href(request: Request, path: str) -> str:
    # Always point base to the deployment prefix root, not the current path.
    # This keeps assets resolving to {prefix}/assets/... for both / and /admin/*.
    prefix = request.scope.get("root_path") or ""
    return (prefix.rstrip("/")) + "/"

def _serve_spa_index(request: Request, path: str) -> HTMLResponse:
    # Read built index and inject a <base> tag so relative assets resolve
    try:
        with open("static/ui/index.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        raise HTTPException(500, "UI not built; run frontend build")
    base = _compute_base_href(request, path)
    if "<base" not in html:
        html = html.replace("<head>", f"<head><base href=\"{base}\">", 1)
    else:
        # best-effort replace existing base href
        import re as _re
        html = _re.sub(r"<base[^>]*href=\"[^\"]*\"", f"<base href=\"{base}\"", html, count=1)
    return HTMLResponse(html)

@app.get("/", include_in_schema=False)
def _spa_index_root_final(request: Request):
    return _serve_spa_index(request, "/")

@app.get("/admin", include_in_schema=False)
def _spa_admin_root(request: Request):
    return _serve_spa_index(request, "/admin")

@app.get("/admin/{rest:path}", include_in_schema=False)
def _spa_admin_catch_all(rest: str, request: Request):
    return _serve_spa_index(request, f"/admin/{rest}")

@app.get("/{path:path}", include_in_schema=False)
def _spa_catch_all_final(path: str, request: Request):
    if _is_spa_path_final(path):
        return _serve_spa_index(request, path)
    raise HTTPException(status_code=404)
