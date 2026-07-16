#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required: https://docs.astral.sh/uv/"
    exit 1
fi

uv sync
uv run python -c "from src.core import ensure_dirs; ensure_dirs()"

echo "Installation complete. Run ./start.sh and open http://127.0.0.1:8889."
