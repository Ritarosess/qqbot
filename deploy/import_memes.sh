#!/usr/bin/env bash
# 把 GitHub 表情包仓库导入到本项目的 emojis/ 目录
# 文件名格式：<中文情绪>_<英文分类>_<序号>.<扩展名>
# EmojiManager 会按 "_" 拆分文件名作为关键词，所以中文和英文都能命中
set -euo pipefail

SRC="${1:-/tmp/aemeath/memes}"
DST="${2:-/opt/qqbot/emojis}"

mkdir -p "$DST"

declare -A ZH=(
  [angry]=生气
  [baka]=笨蛋
  [color]=暧昧
  [confused]=疑惑
  [cpu]=卡住
  [fool]=自嘲
  [givemoney]=打钱
  [happy]=开心
  [like]=喜欢
  [meow]=卖萌
  [morning]=早安
  [reply]=等待
  [sad]=难过
  [see]=偷看
  [shy]=害羞
  [sigh]=叹气
  [sleep]=睡觉
  [surprised]=惊讶
  [work]=工作
)

total=0
for dir in "$SRC"/*/; do
  [ -d "$dir" ] || continue
  cat_name="$(basename "$dir")"
  zh="${ZH[$cat_name]:-$cat_name}"
  n=0
  for f in "$dir"*; do
    [ -f "$f" ] || continue
    n=$((n + 1))
    ext="${f##*.}"
    ext="$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')"
    printf -v idx '%02d' "$n"
    cp -f "$f" "$DST/${zh}_${cat_name}_${idx}.${ext}"
    total=$((total + 1))
  done
  printf '  %-11s -> %-4s %d 张\n' "$cat_name" "$zh" "$n"
done

echo "共导入 ${total} 张，目标目录现有 $(find "$DST" -type f | wc -l) 个文件"
