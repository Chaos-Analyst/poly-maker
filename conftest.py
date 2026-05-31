import os

# Make the test suite self-contained: default to the local docker Postgres
# (docker-compose.yml) unless DATABASE_URL is already set. setdefault means a
# real .env / shell value still wins, and load_dotenv (override=False) won't
# clobber this either.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://poly:poly@localhost:5432/polymaker"
)
