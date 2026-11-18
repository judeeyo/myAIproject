#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  echo '먼저 bash setup.sh 를 실행하세요.' >&2
  exit 2
fi
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i .venv/bin/python -B ui_server.py
fi
exec .venv/bin/python -B ui_server.py
