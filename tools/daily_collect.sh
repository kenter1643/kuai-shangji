#!/bin/zsh
# 快商机 · 每日增量采集（供定时任务调用）
#
# 顺序是有讲究的：
#   1) 两个源的列表 + 详情，拿到当天新公告的关键字段；
#   2) 正文补采，把新公告的正文 / 结构化信息 / 联系人补齐；
#   3) 分类补录，只算空分类，历史结果不动；
#   4) 每步都会重写 data/tenders.json、data/details/、data/contacts.json、data/status.json。
# 跑完之后还需要调用发布工具把 data 推到线上，前端才会看到新数据。
#
# 用法：bash tools/daily_collect.sh
# 试跑（只抓 1 页 1 条，验证链路）：PAGES=1 DETAIL=1 FILL=1 bash tools/daily_collect.sh
set -u

PAGES="${PAGES:-2}"      # 每个地区列表翻页数
DETAIL="${DETAIL:-150}"  # 每个源补抓详情条数
FILL="${FILL:-400}"      # 正文补采条数

PROJ="/Users/kentermacbook/WorkBuddy/2026-10-28-16-13-06/kuai-shangji"
LOGDIR="$PROJ/data/logs"
STAMP=$(date +"%Y-%m-%d")
LOG="$LOGDIR/daily-$STAMP.log"
LOCK="$LOGDIR/daily.lock"

mkdir -p "$LOGDIR"

# 同一时刻只允许一个采集进程，避免定时任务重叠把 SQLite 写坏
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  echo "[$(date '+%F %T')] 已有采集进程 $(cat "$LOCK") 在跑，本次跳过" >> "$LOG"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

cd "$PROJ" || exit 1

say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

say "===== 每日采集开始 ====="

# 步骤执行器：DRY=1 时只打印不真跑，用来验证脚本骨架
run() {
  if [ "${DRY:-0}" = "1" ]; then
    say "DRY-RUN: $*"
    return 0
  fi
  "$@" >> "$LOG" 2>&1
  return $?
}

say "--- 1/4 央采列表 + 详情"
run python3 -m collector.run --source ccgp --pages "$PAGES" --detail "$DETAIL" --delay 1.1
say "央采完成 exit=$?"

say "--- 2/4 粤采列表 + 详情"
run python3 -m collector.run --source gdgpo --pages "$PAGES" --detail "$DETAIL" --delay 1.1
say "粤采完成 exit=$?"

say "--- 3/4 正文补采（新增条目优先）"
run python3 -m collector.run --fill-content "$FILL" --workers 6 --delay 0.3
say "正文补采完成 exit=$?"

say "--- 4/4 行业分类补录（只算空分类）"
run python3 -m collector.run --backfill-category
say "分类补录完成 exit=$?"

say "===== 采集成品 ====="
python3 - <<'PY' >> "$LOG" 2>&1
import json, os
p = os.path.join("data", "status.json")
st = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
print("SUMMARY " + json.dumps(st, ensure_ascii=False))
PY
# 各源的新增条数只在采集日志里，单独摘出来方便一眼看结果
grep -h "本次新增" "$LOG" | tail -5 >> "$LOG" 2>/dev/null
say "新增明细见上（每个源一行）"

say "===== 每日采集结束 ====="
