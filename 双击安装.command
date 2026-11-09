#!/bin/bash
# 双击安装「素材分类」技能：装到 Codex（~/.codex/skills）和 Claude Code（~/.claude/skills），装了哪个就装到哪个；
# 然后检查 ffmpeg 和 Pillow。已有的旧版本挪到 skills 目录外面留着，不删。
cd "$(dirname "$0")" || exit 1
SRC="footage-sorter/skills/footage-sorter"
echo "== 安装素材分类技能 =="
if [ ! -f "$SRC/SKILL.md" ]; then
  echo "找不到 $SRC/SKILL.md，请在解压后的「素材分类」文件夹里双击本脚本。"
  read -n 1 -s -r -p "按任意键关闭"; exit 1
fi

install_to() {  # $1 = ~/.codex 或 ~/.claude
  local base="$1" dst="$1/skills/footage-sorter"
  mkdir -p "$base/skills"
  if [ -e "$dst" ]; then
    mkdir -p "$base/技能旧版"
    local old="$base/技能旧版/footage-sorter-$(date +%Y%m%d-%H%M%S)"
    mv "$dst" "$old"
    echo "  旧版本已挪到：$old"
  fi
  cp -R "$SRC" "$dst"
  echo "  已安装到：$dst"
}

done_any=0
if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
  echo "Codex："; install_to "$HOME/.codex"; done_any=1
fi
if [ -d "$HOME/.claude" ] || command -v claude >/dev/null 2>&1; then
  echo "Claude Code："; install_to "$HOME/.claude"; done_any=1
fi
if [ "$done_any" = 0 ]; then
  echo "没找到 Codex 或 Claude Code。先装其中一个（并至少打开过一次），再双击本脚本。"
  read -n 1 -s -r -p "按任意键关闭"; exit 1
fi

echo
echo "== 检查依赖 =="
python3 "$SRC/scripts/doctor.py"
echo
echo "装好后重开 Codex / Claude Code，对它说：用素材分类技能，把 <素材文件夹> 里的素材分类"
read -n 1 -s -r -p "按任意键关闭"
echo
