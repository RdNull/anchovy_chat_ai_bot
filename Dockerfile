ARG PYTHON_VERSION=3.14
ARG INSTALL_DEV=false

# ---------- builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt requirements-dev.txt /tmp/

ARG INSTALL_DEV
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir -r /tmp/requirements-dev.txt; \
    fi

# ---------- runtime ----------
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
 && apt-get install -y --no-install-recommends libcairo2 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

RUN useradd -m -d /proj -s /bin/bash app
WORKDIR /proj
COPY --chown=app:app . /proj
RUN mkdir -p data && chown -R app:app /proj/data
USER app
