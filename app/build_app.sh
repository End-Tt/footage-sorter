#!/bin/bash
# 编译「素材分类.app」：通用二进制（Apple 芯片 + Intel），macOS 13 起，ad-hoc 签名。
# 引擎 = footage-sorter 技能里的脚本，编译时复制进 app 的 Resources/engine（只维护一份）。
# 产物放在仓库根目录的「素材分类.app」；旧的一份挪进 build/ 留着，不删。需要 Xcode 命令行工具（swiftc）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/app"
SKILL="$ROOT/footage-sorter/skills/footage-sorter"
STAMP=$(date +%Y%m%d-%H%M%S)
B="$ROOT/build/$STAMP"
APP="$B/素材分类.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/engine"

for arch in arm64 x86_64; do
  (cd "$SRC" && swiftc -O -parse-as-library -file-compilation-dir . -debug-prefix-map "$SRC=." \
     -target "$arch-apple-macos13.0" SucaiApp.swift -o "$B/SucaiApp-$arch")
done
lipo -create "$B/SucaiApp-arm64" "$B/SucaiApp-x86_64" -output "$APP/Contents/MacOS/SucaiApp"
cp "$SRC/Info.plist" "$APP/Contents/Info.plist"

# engine: the skill's scripts; vocab.json and the page template sit one level above (common.SKILL_DIR)
cp "$SKILL"/scripts/*.py "$APP/Contents/Resources/engine/"
cp "$SKILL/vocab.json" "$SKILL/catalog_template.html" "$APP/Contents/Resources/"

# icon: render the SVG with QuickLook, then build an .icns
ICON="$B/icon"
mkdir -p "$ICON/AppIcon.iconset"
qlmanage -t -s 1024 -o "$ICON" "$ROOT/footage-sorter/assets/icon.svg" >/dev/null 2>&1 || true
if [ -f "$ICON/icon.svg.png" ]; then
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON/icon.svg.png" --out "$ICON/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2)); sips -z $d $d "$ICON/icon.svg.png" --out "$ICON/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICON/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
fi

codesign --force --deep -s - "$APP"
codesign --verify --deep "$APP" && echo "signed (ad-hoc)"

if [ -d "$ROOT/素材分类.app" ]; then
  mv "$ROOT/素材分类.app" "$ROOT/build/素材分类-replaced-$STAMP.app"
fi
ditto "$APP" "$ROOT/素材分类.app"
echo "built: $ROOT/素材分类.app ($(lipo -archs "$ROOT/素材分类.app/Contents/MacOS/SucaiApp"))"
