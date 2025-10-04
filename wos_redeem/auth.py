from __future__ import annotations

import os
from datetime import timedelta, datetime
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, WebAccount, WebRole


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def ensure_bootstrap_admin(db: Session) -> None:
    # Create an admin account from env if none exists
    admin_user = os.getenv("ADMIN_USERNAME")
    admin_pass = os.getenv("ADMIN_PASSWORD")
    if not admin_user or not admin_pass:
        return
    exists = db.scalar(select(WebAccount).where(WebAccount.role == WebRole.admin.value))
    if exists:
        return
    acct = WebAccount(username=admin_user, password_hash=hash_password(admin_pass), role=WebRole.admin.value)
    db.add(acct)
    db.commit()


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False
