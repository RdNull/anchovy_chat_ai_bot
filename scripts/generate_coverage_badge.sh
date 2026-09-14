#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

docker compose -f "$PROJECT_DIR/docker-compose.yml" exec bot sh -c '
    uv pip install --python /opt/venv/bin/python genbadge defusedxml -q &&
    pytest --tb=no -q --cov=src --cov-report=xml:coverage.xml &&
    genbadge coverage -i coverage.xml -o coverage.svg
'
