# Dashboard database (sign-in, users, roles)

The dashboard can let people sign in with a username and password and
show each of them only some sections. Users, roles and sessions live in
PostgreSQL. The trading engine doesn't use the database.

## What's where

| Piece | File |
|---|---|
| Tables and the list of sections | `ndhd/db.py` |
| Passwords (scrypt), sign-in, sessions, throttling | `ndhd/auth.py` |
| Who may call which route | `api/deps.py` (`require_section`) |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | `api/routers/auth.py` |
| Live feed filtering per user | `api/ws.py` (`visible_part`) |
| Schema changes | `migrations/` (Alembic) |
| Managing users | `scripts/manage_users.py` |

## Ways in

1. **API key** (`X-API-Key` header, or `?api_key=` for the live feed and
   ledger links) matching `DASHBOARD_API_KEY`: full admin, as before.
2. **Sign-in** (only when `DATABASE_URL` is set): an HttpOnly,
   SameSite=Strict session cookie, valid `SESSION_HOURS` (default 12). The
   user's role decides the sections.
3. **Neither set:** no authentication, for 127.0.0.1-only development.

## Sections and roles

| Section | What it covers | admin | viewer |
|---|---|---|---|
| account | Alpaca paper balances, positions, open orders | yes | yes |
| strategy | Stock and wheel panels, risk panel | yes | yes |
| alerts | Alerts and the live engine log | yes | yes |
| congress | Congress trades research | yes | yes |
| watch | Watch-only list | yes | yes |
| robinhood | Robinhood snapshot (personal) | yes | no |
| control | Orders, closing positions, kill switch, config | yes | no |

Admin always sees everything. The viewer's sections can be changed with
`manage_users.py sections viewer --set ...`. The server enforces the
sections on every route and on the live feed; hiding a panel in the page is
only cosmetic on top of that.

## Security notes

- Passwords: scrypt (n=2^15, r=8), at least 12 characters, typed at a
  hidden prompt, never on the command line.
- Sessions: the cookie holds a random token; the database stores only its
  SHA-256, so a database copy doesn't contain usable sessions.
  Changing a password or disabling a user signs them out everywhere.
- Throttling: 5 failed sign-ins for one username within 15 minutes blocks
  further attempts for that username until the window passes.
- Every attempt is logged (`manage_users.py logins`).
- The cookie gets the `Secure` flag when the request arrived over HTTPS
  (directly or via `X-Forwarded-Proto`); force it with
  `SESSION_COOKIE_SECURE=true|false`.

## This PC (set up 2026-10-22)

- PostgreSQL 17.11, Windows service `postgresql-x64-17`, starts with
  Windows, listens on 127.0.0.1 and ::1 only (`listen_addresses =
  'localhost'` via ALTER SYSTEM); `pg_hba.conf` allows local connections only.
- Database `ndhd`, owned by the login role `ndhd_app`.
- `.env` holds `PG_ADMIN_PASSWORD` (the `postgres` superuser),
  `NDHD_DB_PASSWORD` and `DATABASE_URL`. `.env` is never committed.

## Everyday commands (repository root, venv Python)

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head          # after pulling new migrations
.\.venv\Scripts\python.exe scripts\manage_users.py add alice --role viewer
.\.venv\Scripts\python.exe scripts\manage_users.py list
.\.venv\Scripts\python.exe scripts\manage_users.py passwd alice
.\.venv\Scripts\python.exe scripts\manage_users.py disable alice
.\.venv\Scripts\python.exe scripts\manage_users.py remove alice
.\.venv\Scripts\python.exe scripts\manage_users.py sections viewer
.\.venv\Scripts\python.exe scripts\manage_users.py logins
```

Backup: `pg_dump -h 127.0.0.1 -U ndhd_app -Fc ndhd > ndhd.dump`
(`C:\Program Files\PostgreSQL\17\bin\pg_dump.exe`).

## Not done yet

- The dashboard page has no sign-in form yet, and it doesn't hide panels by
  section. Until it does, viewers can sign in only through the API, and the
  page itself still works with the API key as before.
- The server setup script (`deploy/setup-server.sh`) doesn't install
  PostgreSQL yet.
