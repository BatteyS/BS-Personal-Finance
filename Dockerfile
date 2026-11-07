# The page as one container, for a home server: Python 3.12, uv, a user of its own, and the data
# folder mounted at /data. docker-compose.yml runs it behind a Tailscale sidecar; see README.md,
# On a server. The image holds only the code: every export and finance.db live in /data.

FROM python:3.12-slim

# uv, from its own image, pinned to the version the laptop runs.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/

ENV FINANCE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# The page runs as this user, never root. The data folder on the host needs to belong to its
# numeric id (chown 1000:1000) so the page can write finance.db and keep the exports.
RUN useradd --create-home --uid 1000 finance && mkdir /data && chown finance:finance /data

WORKDIR /app

# The packages first, in a layer of their own, so a change to the code doesn't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

# Then the code (see .dockerignore for what stays out), the phone's home-screen icon, which
# launch.sh draws on a Mac and nothing would draw here, and the browser tab's icon in Streamlit's
# own page, which only root can write (icon.py says why Safari needs it).
COPY --chown=finance:finance . .
RUN /app/.venv/bin/python icon.py static/apple-touch-icon.png --favicon && chown -R finance:finance static

USER finance
EXPOSE 8501
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

# Listens on every interface of its network namespace, which docker-compose.yml makes the
# sidecar's: nothing outside the tailnet can reach it.
CMD ["/app/.venv/bin/streamlit", "run", "dashboard.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
