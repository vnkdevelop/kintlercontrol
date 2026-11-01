FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml ./
COPY docker-compose.yml docker-compose.tls.yml docker-compose.caddy.yml ./
COPY server ./server
COPY common ./common
COPY agent ./agent
COPY client ./client
COPY scripts ./scripts
COPY desktop ./desktop
COPY deploy ./deploy
COPY .agents/skills/kintler-remote ./.agents/skills/kintler-remote
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps .

FROM builder AS test
COPY requirements-test.lock ./
RUN /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements-test.lock
COPY tests ./tests
COPY docs ./docs
COPY LICENSE .env.example ./
CMD ["/opt/venv/bin/python", "-m", "pytest", "-q", "--basetemp=/tmp/kintler-tests", "-p", "no:cacheprovider"]

FROM python:3.12-slim-bookworm
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 KINTLER_DATABASE=/app/data/kintler.sqlite3
RUN groupadd --gid 10001 kintler && useradd --uid 10001 --gid kintler --no-create-home --shell /usr/sbin/nologin kintler && mkdir -p /app/data && chown -R kintler:kintler /app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
USER 10001:10001
EXPOSE 7443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7443/healthz',timeout=3)"]
CMD ["kintler-server"]
