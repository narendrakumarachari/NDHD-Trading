"""Dashboard sign-in (ndhd/auth.py, ndhd/db.py, api/deps.py, api/routers/auth.py).

Runs the real Alembic migration on in-memory SQLite, so CI needs no
PostgreSQL. Covers: password hashing, sign-in and throttling, sessions
(expiry, sign-out, disabled users), role sections (viewer can't see
Robinhood or controls), the API key and open modes, and WebSocket filtering.

Run: python -m unittest discover -s tests -t .
"""
import asyncio
import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "ndhd-test-engine.log"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import Response  # noqa: E402

from api import deps, ws  # noqa: E402
from api.routers import auth as auth_router  # noqa: E402
from api.schemas import LoginIn  # noqa: E402
from ndhd import auth, db  # noqa: E402

PASSWORD = "correct horse battery"


def migrated_engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    cfg = Config()  # no ini file: keeps Alembic's logging setup out of the test run
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return engine


def request(cookies=None, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw, "client": ("10.0.0.5", 1234),
                    "scheme": "http", "server": ("testserver", 80), "query_string": b""})


class AuthTestBase(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"DATABASE_URL": "sqlite://", "DASHBOARD_API_KEY": "the-key"})
        self.env.start()
        db.configure(engine=migrated_engine())
        with db.session() as s:
            for name, role in (("ada", db.VIEWER_ROLE), ("root", db.ADMIN_ROLE)):
                s.add(db.User(username=name, password_hash=auth.hash_password(PASSWORD),
                              role=s.scalar(select(db.Role).where(db.Role.name == role))))
            s.commit()

    def tearDown(self):
        self.env.stop()

    def login(self, username="ada", password=PASSWORD):
        with db.session() as s:
            return auth.login(s, username, password, ip="10.0.0.5")


class PasswordTest(unittest.TestCase):
    def test_round_trip_and_wrong_password(self):
        stored = auth.hash_password(PASSWORD)
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verify_password(PASSWORD, stored))
        self.assertFalse(auth.verify_password(PASSWORD + "!", stored))

    def test_same_password_gets_different_salt(self):
        self.assertNotEqual(auth.hash_password(PASSWORD), auth.hash_password(PASSWORD))

    def test_malformed_hash_is_rejected_not_raised(self):
        for bad in ("", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$aa$bb"):
            self.assertFalse(auth.verify_password(PASSWORD, bad))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError):
            auth.check_password_rules("short")


class LoginTest(AuthTestBase):
    def test_viewer_sees_default_sections_only(self):
        token = self.login()
        with db.session() as s:
            p = auth.principal_for_token(s, token)
        self.assertEqual(p.sections, frozenset(db.VIEWER_DEFAULT_SECTIONS))
        self.assertNotIn("robinhood", p.sections)
        self.assertNotIn("control", p.sections)

    def test_admin_sees_everything(self):
        with db.session() as s:
            p = auth.principal_for_token(s, self.login("root"))
        self.assertEqual(p.sections, frozenset(db.SECTIONS))

    def test_wrong_password_and_unknown_user_fail_and_are_logged(self):
        self.assertIsNone(self.login(password="nope"))
        self.assertIsNone(self.login(username="nobody"))
        with db.session() as s:
            events = s.scalars(select(db.LoginEvent)).all()
        self.assertEqual([e.success for e in events], [False, False])

    def test_only_token_hash_is_stored(self):
        token = self.login()
        with db.session() as s:
            stored = s.scalars(select(db.LoginSession.token_hash)).all()
        self.assertEqual(stored, [auth.token_hash(token)])
        self.assertNotIn(token, stored)

    def test_throttled_after_repeated_failures(self):
        for _ in range(auth.MAX_FAILED_LOGINS):
            self.assertIsNone(self.login(password="nope"))
        with self.assertRaises(auth.LoginThrottled):
            self.login()

    def test_disabled_user_cannot_sign_in_and_loses_sessions(self):
        token = self.login()
        with db.session() as s:
            user = s.scalar(select(db.User).where(db.User.username == "ada"))
            user.is_active = False
            auth.revoke_user_sessions(s, user)
            s.commit()
            self.assertIsNone(auth.principal_for_token(s, token))
        self.assertIsNone(self.login())

    def test_expired_and_signed_out_sessions_are_refused(self):
        expired, live = self.login(), self.login()
        with db.session() as s:
            s.get(db.LoginSession, auth.token_hash(expired)).expires_at = db.utcnow() - timedelta(seconds=1)
            s.commit()
            self.assertIsNone(auth.principal_for_token(s, expired))
            auth.logout(s, live)
            self.assertIsNone(auth.principal_for_token(s, live))

    def test_role_sections_can_be_changed(self):
        with db.session() as s:
            viewer = s.scalar(select(db.Role).where(db.Role.name == db.VIEWER_ROLE))
            viewer.sections = [db.RoleSection(section="watch")]
            s.commit()
            self.assertEqual(auth.principal_for_token(s, self.login()).sections, frozenset({"watch"}))


class SectionGateTest(AuthTestBase):
    def check(self, section, cookies=None, headers=None):
        principal = deps.current_principal(request(cookies, headers), (headers or {}).get("X-API-Key"))
        return deps.require_section(section)(principal)

    def test_no_credentials_is_401(self):
        with self.assertRaises(HTTPException) as ctx:
            self.check("watch")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_viewer_cookie_passes_watch_but_not_robinhood_or_control(self):
        cookies = {deps.SESSION_COOKIE: self.login()}
        self.assertEqual(self.check("watch", cookies).username, "ada")
        for section in ("robinhood", "control"):
            with self.assertRaises(HTTPException) as ctx:
                self.check(section, cookies)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_api_key_is_full_admin_and_wrong_key_is_401(self):
        self.assertEqual(self.check("control", headers={"X-API-Key": "the-key"}).via, "api_key")
        with self.assertRaises(HTTPException) as ctx:
            self.check("watch", headers={"X-API-Key": "wrong"})
        self.assertEqual(ctx.exception.status_code, 401)

    def test_unknown_section_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            deps.require_section("nope")


class CongressPullTest(unittest.TestCase):
    """A Congress pull is costly: viewers may read the data, only admins may
    start a pull."""

    def gate_results(self, method, principal):
        from api.routers import congress
        route = next(r for r in congress.router.routes
                     if r.path == "/api/congress/refresh" and method in r.methods)
        results = []
        for dep in route.dependant.dependencies:
            try:
                dep.call(principal=principal)
                results.append(200)
            except HTTPException as exc:
                results.append(exc.status_code)
        return results

    def test_viewer_can_read_status_but_not_start_a_pull(self):
        viewer = auth.Principal("ada", db.VIEWER_ROLE, frozenset(db.VIEWER_DEFAULT_SECTIONS), "session")
        self.assertEqual(self.gate_results("GET", viewer), [200])
        self.assertIn(403, self.gate_results("POST", viewer))

    def test_admin_can_start_a_pull(self):
        admin = auth.Principal("root", db.ADMIN_ROLE, frozenset(db.SECTIONS), "session")
        self.assertEqual(set(self.gate_results("POST", admin)), {200})


class ModesTest(unittest.TestCase):
    def test_open_only_when_neither_key_nor_database(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(deps.principal_from(None, None).via, "open")
        with mock.patch.dict(os.environ, {"DASHBOARD_API_KEY": "k"}, clear=True):
            self.assertIsNone(deps.principal_from(None, None))


class LoginRouteTest(AuthTestBase):
    def test_login_sets_httponly_strict_cookie(self):
        response = Response()
        me = auth_router.login(LoginIn(username="ada", password=PASSWORD), request(), response)
        self.assertTrue(me.signed_in)
        self.assertEqual(me.role, db.VIEWER_ROLE)
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{deps.SESSION_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("Secure", cookie)  # plain http (127.0.0.1)

    def test_cookie_is_secure_behind_https_proxy(self):
        response = Response()
        auth_router.login(LoginIn(username="ada", password=PASSWORD),
                          request(headers={"X-Forwarded-Proto": "https"}), response)
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_wrong_password_is_401_and_throttle_is_429(self):
        for _ in range(auth.MAX_FAILED_LOGINS):
            with self.assertRaises(HTTPException) as ctx:
                auth_router.login(LoginIn(username="ada", password="nope"), request(), Response())
            self.assertEqual(ctx.exception.status_code, 401)
        with self.assertRaises(HTTPException) as ctx:
            auth_router.login(LoginIn(username="ada", password=PASSWORD), request(), Response())
        self.assertEqual(ctx.exception.status_code, 429)


class WebSocketFilterTest(unittest.TestCase):
    def test_messages_follow_sections(self):
        tick = {"type": "tick", "account": {}, "alerts": [1]}
        viewer = frozenset({"account"})
        self.assertEqual(ws.visible_part(tick, viewer), {"type": "tick", "account": {}})
        self.assertIsNone(ws.visible_part({"type": "strategy"}, viewer))
        self.assertIsNone(ws.visible_part({"type": "log", "entries": []}, viewer))
        self.assertEqual(ws.visible_part({"type": "error"}, frozenset()), {"type": "error"})
        self.assertEqual(ws.visible_part(tick, frozenset(db.SECTIONS)), tick)


class FakeSocket:
    def __init__(self):
        self.close_code = None

    async def accept(self):
        pass

    async def close(self, code=1000):
        self.close_code = code


class WebSocketRecheckTest(AuthTestBase):
    """Open live connections are checked again (api/ws.py recheck), so a
    disabled user or ended session stops getting data without a reload."""

    def open(self, manager, token=None, api_key=None):
        principal = deps.principal_from(api_key, token)
        sock = FakeSocket()
        asyncio.run(manager.connect(sock, principal.sections, api_key, token))
        return sock

    def set_user_active(self, username, active):
        with db.session() as s:
            user = s.scalar(select(db.User).where(db.User.username == username))
            user.is_active = active
            if not active:
                auth.revoke_user_sessions(s, user)
            s.commit()

    def test_unchanged_sign_in_stays_open(self):
        manager = ws.ConnectionManager()
        sock = self.open(manager, token=self.login())
        asyncio.run(manager.recheck())
        self.assertIsNone(sock.close_code)
        self.assertIn(sock, manager.active)

    def test_disabled_user_is_cut_off(self):
        manager = ws.ConnectionManager()
        sock = self.open(manager, token=self.login())
        self.set_user_active("ada", False)
        asyncio.run(manager.recheck())
        self.assertEqual(sock.close_code, ws.CLOSE_SIGNED_OUT)
        self.assertNotIn(sock, manager.active)

    def test_signed_out_and_expired_sessions_are_cut_off(self):
        manager = ws.ConnectionManager()
        signed_out, expired = self.login(), self.login()
        socks = [self.open(manager, token=signed_out), self.open(manager, token=expired)]
        with db.session() as s:
            auth.logout(s, signed_out)
            s.get(db.LoginSession, auth.token_hash(expired)).expires_at = db.utcnow() - timedelta(seconds=1)
            s.commit()
        asyncio.run(manager.recheck())
        self.assertEqual([k.close_code for k in socks], [ws.CLOSE_SIGNED_OUT] * 2)
        self.assertEqual(manager.active, {})

    def test_role_change_closes_with_its_own_code(self):
        manager = ws.ConnectionManager()
        sock = self.open(manager, token=self.login())
        with db.session() as s:
            viewer = s.scalar(select(db.Role).where(db.Role.name == db.VIEWER_ROLE))
            viewer.sections = [db.RoleSection(section="watch")]
            s.commit()
        asyncio.run(manager.recheck())
        self.assertEqual(sock.close_code, ws.CLOSE_ROLE_CHANGED)

    def test_api_key_connection_stays_until_the_key_changes(self):
        manager = ws.ConnectionManager()
        sock = self.open(manager, api_key="the-key")
        asyncio.run(manager.recheck())
        self.assertIsNone(sock.close_code)
        with mock.patch.dict(os.environ, {"DASHBOARD_API_KEY": "a-new-key"}):
            asyncio.run(manager.recheck())
        self.assertEqual(sock.close_code, ws.CLOSE_SIGNED_OUT)

    def test_database_error_fails_closed(self):
        manager = ws.ConnectionManager()
        sock = self.open(manager, token=self.login())
        with mock.patch.object(deps, "principal_from", side_effect=OperationalError("x", {}, Exception("down"))):
            asyncio.run(manager.recheck())
        self.assertEqual(sock.close_code, ws.CLOSE_SIGNED_OUT)


if __name__ == "__main__":
    unittest.main()
