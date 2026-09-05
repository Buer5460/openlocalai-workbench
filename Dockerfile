# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

LABEL org.opencontainers.image.title="OpenLocalAI Workbench" \
      org.opencontainers.image.description="Offline, auditable AI workbench for regulated organizations" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN groupadd --gid "${APP_GID}" openlocalai \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin openlocalai \
    && install -d -m 0700 -o "${APP_UID}" -g "${APP_GID}" /data

COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && python -c "import openlocalai; print(openlocalai.__version__)"

USER ${APP_UID}:${APP_GID}

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).read()"]

ENTRYPOINT ["openlocalai"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--data", "/data/openlocalai.db"]
