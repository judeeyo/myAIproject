#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ktx_python=''
for candidate in \
  .venv/bin/python \
  python3 python3.15 python3.14 python3.13 python3.12 python3.11 \
  /opt/homebrew/bin/python3 /usr/local/bin/python3 \
  /opt/homebrew/opt/python@3.{15,14,13,12,11}/bin/python3.{15,14,13,12,11} \
  /usr/local/opt/python@3.{15,14,13,12,11}/bin/python3.{15,14,13,12,11} \
  /Library/Frameworks/Python.framework/Versions/{Current,3.15,3.14,3.13,3.12,3.11}/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    ktx_python="$candidate"
    break
  fi
done
if [ -z "$ktx_python" ]; then
  echo 'Python 3.11 이상을 찾지 못했습니다.' >&2
  echo 'https://www.python.org/downloads/macos/ 에서 Python 3.13 이상을 설치하세요.' >&2
  echo '설치 후 터미널을 다시 열고 bash setup.sh 를 실행하세요.' >&2
  echo 'Homebrew가 있다면 brew install python@3.13 으로 설치해도 됩니다.' >&2
  exit 2
fi
"$ktx_python" --version
if [ ! -x .venv/bin/python ]; then
  "$ktx_python" -m venv .venv
fi
if ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo '기존 .venv가 Python 3.11 미만입니다. .venv 폴더의 이름을 바꾼 뒤 다시 실행하세요.' >&2
  exit 2
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -B -m unittest discover -s tests -v
