#!/usr/bin/env bash
# macOS: Finder에서 더블클릭하면 실행됩니다.
here="$(cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$here/ktx_reserve/launch.sh" ]; then
  exec bash "$here/ktx_reserve/launch.sh"
fi
exec bash "$here/launch.sh"
