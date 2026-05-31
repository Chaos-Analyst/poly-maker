"""Pytest bootstrap: isolate the suite onto a dedicated test database.

Several tests call db.write_markets / db.init_db, which create and *replace* the
real markets / risk_state / summary tables. To keep the suite from ever clobbering
live data, redirect DATABASE_URL to a "<db>_test" database on the same Postgres
server before any engine is built. The redirect happens unconditionally, so even if
creating the test DB fails, tests hit the (missing) test DB and error out rather than
writing to production.
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

# Derive the test DB from the real configured DATABASE_URL (load .env so we see it).
load_dotenv()
_base = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://poly:poly@localhost:5432/polymaker"
)
_url = make_url(_base)
_test_db = (_url.database or "polymaker") + "_test"

# Redirect first — this alone protects production no matter what happens below.
os.environ["DATABASE_URL"] = _url.set(database=_test_db).render_as_string(hide_password=False)

# Best-effort: create the test database if it does not already exist.
try:
    _admin = create_engine(_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with _admin.connect() as _conn:
        if not _conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": _test_db}
        ).scalar():
            _conn.execute(text(f'CREATE DATABASE "{_test_db}"'))
    _admin.dispose()
except Exception as _exc:  # pragma: no cover - surfaced when tests then fail to connect
    print(f"conftest: could not ensure test database {_test_db!r}: {_exc}")

# In case anything already built an engine against the old URL, force a rebuild.
import poly_utils.db as _db  # noqa: E402

_db._engine = None
