#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ktx_args=(--dep 서울 --arr 동대구 --date 2026-11-06 --from 13:49 --to 13:58 --adult 2 --seat general-only --interval-min 1 --interval-max 2 --pay)
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i bash run.sh "${ktx_args[@]}"
fi
exec bash run.sh "${ktx_args[@]}"
