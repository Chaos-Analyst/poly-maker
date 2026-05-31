# One image for both poly-maker processes (the trading bot and the market updater).
# Each docker-compose service runs this image with a different command.
FROM python:3.9-slim

# uv for fast, reproducible installs (pinned by uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Dependency layer first so it stays cached unless pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# Then the application code, and install the project itself.
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Default command is the bot; the updater service overrides it.
CMD ["python", "main.py"]
