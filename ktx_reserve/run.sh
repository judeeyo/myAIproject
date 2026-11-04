#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [ ! -x .venv/bin/python ]; then
  echo '먼저 bash setup.sh 를 실행하세요.' >&2
  exit 2
fi
exec .venv/bin/python -B ktx_reserve.py "$@"
