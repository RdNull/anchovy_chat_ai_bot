#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

docker compose -f "$PROJECT_DIR/docker-compose.yml" exec bot sh -c '
    pip install genbadge defusedxml -q &&
    pytest --tb=no -q --cov=src --cov-report=xml:coverage.xml &&
    /proj/.local/bin/genbadge coverage -i coverage.xml -o coverage.svg
'
