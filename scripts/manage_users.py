"""Manage dashboard sign-in users (PostgreSQL, see ndhd/db.py).

Run from the repository root with the venv's Python. Passwords are typed at
a hidden prompt (never on the command line, where they'd land in shell
history); --password-stdin reads one line instead, for scripts.

    python scripts/manage_users.py list
    python scripts/manage_users.py add alice --role viewer
    python scripts/manage_users.py passwd alice
    python scripts/manage_users.py role alice admin
    python scripts/manage_users.py disable alice      (also signs them out)
    python scripts/manage_users.py enable alice
    python scripts/manage_users.py remove alice       (sign-in log is kept)
    python scripts/manage_users.py sections viewer                  (show)
    python scripts/manage_users.py sections viewer --set account,strategy,watch
    python scripts/manage_users.py logins --limit 20
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402

from ndhd import auth, db  # noqa: E402


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("New password: ")
        if getpass.getpass("Repeat it: ") != password:
            raise SystemExit("The passwords don't match.")
    try:
        auth.check_password_rules(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    return password


def get_user(s, username: str) -> db.User:
    user = s.scalar(select(db.User).where(db.User.username == username))
    if user is None:
        raise SystemExit(f"No user named {username!r}.")
    return user


def get_role(s, name: str) -> db.Role:
    role = s.scalar(select(db.Role).where(db.Role.name == name))
    if role is None:
        names = ", ".join(s.scalars(select(db.Role.name)))
        raise SystemExit(f"No role named {name!r}. Roles: {names}")
    return role


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage dashboard sign-in users.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="users, roles and last sign-in")
    p = sub.add_parser("add", help="create a user")
    p.add_argument("username")
    p.add_argument("--role", default=db.VIEWER_ROLE)
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("passwd", help="set a new password (signs the user out everywhere)")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("role", help="change a user's role")
    p.add_argument("username")
    p.add_argument("role")
    for name in ("disable", "enable", "remove"):
        sub.add_parser(name, help=f"{name} a user").add_argument("username")
    p = sub.add_parser("sections", help="show or set the sections a role may see")
    p.add_argument("role")
    p.add_argument("--set", help="comma-separated: " + ",".join(db.SECTIONS))
    p = sub.add_parser("logins", help="recent sign-in attempts")
    p.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    load_dotenv()
    if not db.database_url():
        raise SystemExit("DATABASE_URL is not set (see .env).")

    with db.session() as s:
        if args.cmd == "list":
            for u in s.scalars(select(db.User).order_by(db.User.username)):
                last = (u.last_login_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        if u.last_login_at else "never")
                state = "active" if u.is_active else "DISABLED"
                print(f"{u.username:20} {u.role.name:10} {state:9} last sign-in: {last}")
        elif args.cmd == "add":
            if s.scalar(select(db.User).where(db.User.username == args.username)):
                raise SystemExit(f"User {args.username!r} already exists.")
            role = get_role(s, args.role)
            s.add(db.User(username=args.username, role=role,
                          password_hash=auth.hash_password(read_password(args.password_stdin))))
            s.commit()
            print(f"Added {args.username} ({role.name}).")
        elif args.cmd == "passwd":
            user = get_user(s, args.username)
            user.password_hash = auth.hash_password(read_password(args.password_stdin))
            n = auth.revoke_user_sessions(s, user)
            s.commit()
            print(f"Password changed for {user.username}; {n} session(s) signed out.")
        elif args.cmd == "role":
            user = get_user(s, args.username)
            user.role = get_role(s, args.role)
            s.commit()
            print(f"{user.username} is now {user.role.name}.")
        elif args.cmd in ("disable", "enable"):
            user = get_user(s, args.username)
            user.is_active = args.cmd == "enable"
            n = auth.revoke_user_sessions(s, user) if not user.is_active else 0
            s.commit()
            print(f"{user.username} {args.cmd}d" + (f"; {n} session(s) signed out." if n else "."))
        elif args.cmd == "remove":
            user = get_user(s, args.username)
            auth.revoke_user_sessions(s, user)
            for e in s.scalars(select(db.LoginEvent).where(db.LoginEvent.user_id == user.id)):
                e.user_id = None
            s.query(db.LoginSession).filter(db.LoginSession.user_id == user.id).delete()
            s.delete(user)
            s.commit()
            print(f"Removed {args.username}.")
        elif args.cmd == "sections":
            role = get_role(s, args.role)
            if args.set is not None:
                if role.name == db.ADMIN_ROLE:
                    raise SystemExit("The admin role always sees every section.")
                wanted = {x.strip() for x in args.set.split(",") if x.strip()}
                unknown = wanted - set(db.SECTIONS)
                if unknown:
                    raise SystemExit(f"Unknown section(s): {', '.join(sorted(unknown))}")
                role.sections = [db.RoleSection(section=x) for x in sorted(wanted)]
                s.commit()
            have = db.role_sections(s, role)
            for name, what in db.SECTIONS.items():
                print(f"[{'x' if name in have else ' '}] {name:10} {what}")
        elif args.cmd == "logins":
            rows = s.scalars(select(db.LoginEvent).order_by(db.LoginEvent.at.desc()).limit(args.limit))
            for e in rows:
                at = e.at.astimezone(timezone.utc) if e.at.tzinfo else e.at
                print(f"{at:%Y-%m-%d %H:%M:%S} UTC {'ok  ' if e.success else 'FAIL'} {e.username:20} {e.ip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
