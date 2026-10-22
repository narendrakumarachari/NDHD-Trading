"""Passwords, sign-in and sessions for the dashboard (tables in ndhd/db.py).

Passwords are hashed with scrypt from the standard library (no extra
dependency), stored as "scrypt$n$r$p$salt$hash" so the cost can be raised
later without breaking existing hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
MIN_PASSWORD_LENGTH = 12
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
# More failed attempts than this for one username inside the window, and
# further attempts are refused until it passes.
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_WINDOW = timedelta(minutes=15)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=128 * SCRYPT_N * SCRYPT_R * 2)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=n, r=r, p=p,
                                maxmem=128 * n * r * 2, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked against for unknown usernames so a miss takes as long as a wrong
# password and timing doesn't reveal which usernames exist.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who is making a request, and what they may see."""
    username: str
    role: str
    sections: frozenset[str]
    via: str  # "session", "api_key" or "open" (no auth configured)

    @property
    def is_admin(self) -> bool:
        return self.role == db.ADMIN_ROLE


class LoginThrottled(Exception):
    pass


def recent_failures(s: Session, username: str) -> int:
    since = db.utcnow() - FAILED_LOGIN_WINDOW
    return s.scalar(
        select(func.count()).select_from(db.LoginEvent).where(
            db.LoginEvent.username == username,
            db.LoginEvent.success.is_(False),
            db.LoginEvent.at >= since,
        )
    ) or 0


def login(s: Session, username: str, password: str, ip: str = "", user_agent: str = "") -> Optional[str]:
    """A new session token on success, None on a wrong username or password.
    Raises LoginThrottled after too many recent failures. Every attempt is
    logged in login_events."""
    username = username.strip()
    if recent_failures(s, username) >= MAX_FAILED_LOGINS:
        raise LoginThrottled(username)

    user = s.scalar(select(db.User).where(db.User.username == username))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    ok = ok and user is not None and user.is_active

    s.add(db.LoginEvent(username=username[:80], user_id=user.id if user else None, success=ok,
                        ip=ip[:64], user_agent=user_agent[:300]))
    if not ok:
        s.commit()
        return None

    token = secrets.token_urlsafe(32)
    now = db.utcnow()
    s.add(db.LoginSession(token_hash=token_hash(token), user_id=user.id, created_at=now,
                          expires_at=now + timedelta(hours=SESSION_HOURS)))
    user.last_login_at = now
    s.commit()
    return token


def _aware(dt):
    # SQLite hands back naive datetimes; PostgreSQL keeps the timezone.
    return dt if dt.tzinfo else dt.replace(tzinfo=db.utcnow().tzinfo)


def principal_for_token(s: Session, token: str) -> Optional[Principal]:
    row = s.get(db.LoginSession, token_hash(token))
    if row is None or row.revoked or _aware(row.expires_at) <= db.utcnow():
        return None
    user = row.user
    if not user.is_active:
        return None
    return Principal(user.username, user.role.name, frozenset(db.role_sections(s, user.role)), "session")


def logout(s: Session, token: str) -> None:
    row = s.get(db.LoginSession, token_hash(token))
    if row is not None:
        row.revoked = True
        s.commit()


def revoke_user_sessions(s: Session, user: db.User) -> int:
    rows = s.scalars(select(db.LoginSession).where(db.LoginSession.user_id == user.id,
                                                   db.LoginSession.revoked.is_(False))).all()
    for row in rows:
        row.revoked = True
    return len(rows)
