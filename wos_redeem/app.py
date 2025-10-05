from __future__ import annotations

import os
from datetime import datetime
import re
import os
from typing import Optional

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, Response
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse
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
from .tasks import start_background_threads, MIN_RETRY_MINUTES


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
    success = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "success")) or 0
    failed = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "failed")) or 0
    pending = db.scalar(select(func.count(Redemption.id)).where(Redemption.status == "pending")) or 0
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
    tag_val = (tag or "").strip()
    if not re.fullmatch(r"[A-Za-z]{3}", tag_val):
        raise HTTPException(status_code=400, detail="tag must be exactly 3 letters")
    a = Alliance(name=name.strip(), tag=tag_val, quota=max(0, int(quota)))
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
        a.name = name.strip()
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
    # current from status file
    import json as _json
    current = None
    try:
        with open(".worker_status") as f:
            w = _json.loads(f.read())
            current = w.get("current")
    except Exception:
        current = None

    # recent attempts joined with user fid and code
    recents = []
    q = (
        db.query(RedemptionAttempt, Redemption, User, GiftCode)
        .join(Redemption, RedemptionAttempt.redemption_id == Redemption.id)
        .join(User, Redemption.user_id == User.id)
        .join(GiftCode, Redemption.gift_code_id == GiftCode.id)
        .order_by(RedemptionAttempt.created_at.desc())
        .limit(max(1, min(10, limit)))
    )
    for att, red, user, code in q.all():
        recents.append({
            "id": att.id,
            "ts": att.created_at.isoformat() if att.created_at else None,
            "fid": user.fid,
            "code": code.code,
            "err": att.err_code,
            "msg": att.result_msg[:120] if att.result_msg else None,
        })

    # build a tiny queue preview
    upcoming = []
    cutoff = datetime.utcnow()
    if MIN_RETRY_MINUTES:
        from datetime import timedelta as _td
        cutoff = datetime.utcnow() - _td(minutes=MIN_RETRY_MINUTES)

    active_codes = db.scalars(select(GiftCode).where(GiftCode.active == True).order_by(GiftCode.first_seen_at.asc()).limit(20)).all()
    active_users = db.scalars(select(User).where(User.active == True).order_by(User.id.asc()).limit(50)).all()

    for code in active_codes:
        if len(upcoming) >= limit:
            break
        for user in active_users:
            if len(upcoming) >= limit:
                break
            red = db.scalar(select(Redemption).where(Redemption.user_id == user.id, Redemption.gift_code_id == code.id))
            if red and red.status == "success":
                continue
            if red and red.last_attempt_at and red.last_attempt_at > cutoff:
                continue
            upcoming.append({"fid": user.fid, "user_id": user.id, "code": code.code, "gift_code_id": code.id})

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
def api_create_user(fid: int = Form(...), name: Optional[str] = Form(None), alliance_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    if not DISABLE_AUTH_ALL:
        _require(True)
    if db.scalar(select(User).where(User.fid == fid)):
        return {"ok": True, "existing": True}
    u = User(fid=fid, name=name or None, alliance_id=alliance_id if alliance_id else None)
    db.add(u)
    db.commit()
    return {"ok": True, "id": u.id}


@app.get("/api/codes")
def api_codes(db: Session = Depends(get_db)):
    rows = db.scalars(select(GiftCode).order_by(GiftCode.first_seen_at.desc()).limit(200)).all()
    return [{"id": c.id, "code": c.code, "active": c.active, "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None} for c in rows]


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
