ARG PYTHON_VERSION=3.14
ARG INSTALL_DEV=false
ARG UV_VERSION=0.12.13

# ---------- uv binary ----------
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /src
COPY pyproject.toml uv.lock /src/

ARG INSTALL_DEV
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        uv sync --frozen; \
    else \
        uv sync --frozen --no-dev; \
    fi

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends libcairo2 \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -d /proj -s /bin/bash app
COPY --chown=app:app --from=builder /opt/venv /opt/venv
WORKDIR /proj
COPY --chown=app:app . /proj
RUN mkdir -p data && chown -R app:app /proj/data
USER app
