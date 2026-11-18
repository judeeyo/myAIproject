#!/usr/bin/env bash
# 원클릭 실행: 처음이면 설치하고, 설정 화면을 연다.
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

fail() {
  echo
  echo "❌ $1"
  echo "   엔터를 누르면 창을 닫습니다."
  read -r _ || true
  exit 1
}

has_python() {
  local candidate
  for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    /Library/Frameworks/Python.framework/Versions/{Current,3.15,3.14,3.13,3.12,3.11}/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ] || ! cmp -s requirements.txt .venv/.ktx-ready; then
  if [ ! -x .venv/bin/python ] && ! has_python; then
    echo "▶ Python 3.11 이상이 필요합니다."
    if command -v brew >/dev/null 2>&1; then
      echo "▶ Homebrew로 Python을 설치합니다."
      brew install python@3.13 || fail "Python 설치에 실패했습니다."
    else
      command -v open >/dev/null 2>&1 && open "https://www.python.org/downloads/macos/"
      fail "열린 페이지에서 Python 3.13 설치 파일(.pkg)을 받아 설치한 뒤, 이 파일을 다시 실행하세요."
    fi
  fi
  if ! git --version >/dev/null 2>&1; then
    fail "Git이 필요합니다. 설치 창이 뜨면 설치를 마친 뒤 다시 실행하세요. (창이 안 뜨면 터미널에서 xcode-select --install)"
  fi
  echo "▶ 처음 실행이라 필요한 프로그램을 설치합니다 (1~3분, 한 번만)."
  bash setup.sh || fail "설치에 실패했습니다. 위 메시지를 확인하세요."
  cp requirements.txt .venv/.ktx-ready
  echo "✅ 설치 완료"
fi

echo "▶ 설정 화면을 엽니다. 이 창을 닫으면 프로그램이 종료됩니다."
bash ui.sh || fail "화면을 실행하지 못했습니다. 위 메시지를 확인하세요."
