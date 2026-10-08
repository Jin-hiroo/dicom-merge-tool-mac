#!/usr/bin/env bash
# head3Dv1.app をビルドする (macOS / Apple Silicon)。
#
#   ./build.sh                  ビルド → セルフテスト → バンドル検査 → DMG
#   ./build.sh --skip-dmg       DMG を作らない
#   ./build.sh --skip-selftest  セルフテストを省く
#   ./build.sh --no-gui-test    セルフテストでウィンドウを開かない
#
# 環境変数:
#   PYTHON       venv の作成に使う Python 3.12 (既定: 自動検出)
#   APP_VERSION  アプリのバージョン (既定: 1.0.0)
#
# 成果物: dist/head3Dv1.app, dist/head3Dv1-<version>-arm64.dmg (+ .sha256)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv-build"
APP="$ROOT/dist/head3Dv1.app"
export APP_VERSION="${APP_VERSION:-1.0.0}"
export PYTHONDONTWRITEBYTECODE=1        # upstream/ に __pycache__ を作らない
export PIP_DISABLE_PIP_VERSION_CHECK=1

SKIP_DMG=0
SKIP_SELFTEST=0
GUI_TEST=1
for arg in "$@"; do
  case "$arg" in
    --skip-dmg) SKIP_DMG=1 ;;
    --skip-selftest) SKIP_SELFTEST=1 ;;
    --no-gui-test) GUI_TEST=0 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "不明なオプション: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nエラー: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "macOS でのみビルドできます。"
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon (arm64) の Mac でビルドしてください。"

# ---------------------------------------------------------------------------
step "元コード (upstream/) を確認"
if [[ ! -f upstream/app/main.py ]]; then
  [[ -e .git ]] || die "upstream/ が空です。ZIP ではなく git clone --recursive で取得してください。"
  git submodule update --init upstream
fi
[[ -f upstream/app/main.py ]] || die "upstream/ を取得できませんでした。"
echo "upstream: $(git -C upstream rev-parse --short HEAD 2>/dev/null || echo '?')"

# ---------------------------------------------------------------------------
step "Python 3.12 を探す"
if [[ -z "${PYTHON:-}" ]]; then
  for candidate in python3.12 \
                   /opt/homebrew/bin/python3.12 \
                   /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi
[[ -n "${PYTHON:-}" ]] || die "Python 3.12 が見つかりません。
  brew install python@3.12
または https://www.python.org/downloads/macos/ から 3.12 を入れてください。"
"$PYTHON" - <<'EOF' || die "上記の理由でこの Python は使えません。PYTHON=... で別の Python 3.12 を指定してください。"
import os, platform, sys
problems = []
if sys.version_info[:2] != (3, 12):
    problems.append(f"Python {platform.python_version()} です (3.12 が必要)")
if platform.machine() != "arm64":
    problems.append(f"{platform.machine()} 版です (Rosetta ではなく arm64 版が必要)")
if os.path.isdir(os.path.join(sys.base_prefix, "conda-meta")):
    problems.append("Anaconda / conda の Python です (Qt プラグインが混ざるため不可)")
for p in problems:
    print("  -", p, file=sys.stderr)
print(sys.executable, platform.python_version(), platform.machine())
sys.exit(1 if problems else 0)
EOF

# ---------------------------------------------------------------------------
step "ビルド用 venv を用意 (.venv-build)"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet \
  -r upstream/requirements.txt -r requirements-build.txt

# ---------------------------------------------------------------------------
step "アイコンを生成"
"$VENV/bin/python" macos/make_icon.py build/icon

step "PyInstaller でバンドル"
rm -rf "$APP" dist/head3Dv1
"$VENV/bin/python" -m PyInstaller --clean --noconfirm --log-level WARN macos/head3Dv1.spec
[[ -x "$APP/Contents/MacOS/head3Dv1" ]] || die "$APP が作られませんでした。"
du -sh "$APP"

# ---------------------------------------------------------------------------
WORK="$(mktemp -d -t head3Dv1-build)"
trap 'rm -rf "$WORK"' EXIT

if [[ "$SKIP_SELFTEST" == 0 ]]; then
  step "セルフテスト (合成 DICOM で読込 → STL 書出し$([[ $GUI_TEST == 1 ]] && echo ' → ウィンドウ表示'))"
  "$VENV/bin/python" upstream/tests/make_phantom.py "$WORK/phantom" >/dev/null
  if [[ "$GUI_TEST" == 1 ]]; then
    HEAD3DV1_DATA_DIR="$WORK/data" "$APP/Contents/MacOS/head3Dv1" \
      --self-test --dicom "$WORK/phantom/seriesA"
  else
    HEAD3DV1_DATA_DIR="$WORK/data" "$APP/Contents/MacOS/head3Dv1" \
      --self-test --dicom "$WORK/phantom/seriesA" --no-gui
  fi
fi

# 起動してもバンドル自体が書き換わっていないことも含めて確認する
step "バンドル検査 (arm64 / 最小 macOS / 署名)"
"$VENV/bin/python" macos/check_bundle.py "$APP"

# ---------------------------------------------------------------------------
if [[ "$SKIP_DMG" == 0 ]]; then
  step "DMG を作成"
  DMG="$ROOT/dist/head3Dv1-$APP_VERSION-arm64.dmg"
  mkdir -p "$WORK/dmg"
  ditto "$APP" "$WORK/dmg/head3Dv1.app"
  ln -s /Applications "$WORK/dmg/Applications"
  rm -f "$DMG"
  # hdiutil はまれに "Resource busy" で失敗するので数回試す
  for attempt in 1 2 3; do
    if hdiutil create -quiet -volname "head3Dv1" -srcfolder "$WORK/dmg" \
         -ov -format UDZO "$DMG"; then
      break
    fi
    [[ "$attempt" == 3 ]] && die "DMG を作成できませんでした。"
    echo "hdiutil に失敗しました。再試行します ($attempt/3)"
    sleep 5
  done
  # アプリ内アップデートはこのチェックサムで改ざん・破損を検出する
  (cd "$(dirname "$DMG")" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
  du -sh "$DMG"
fi

step "完了"
echo "  アプリ: $APP"
[[ "$SKIP_DMG" == 0 ]] && echo "  DMG   : $DMG"
exit 0
