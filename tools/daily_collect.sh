#!/bin/zsh
# 快商机 · 每日增量采集（供定时任务调用）
#
# 顺序是有讲究的：
#   1) 央采列表 + 详情；
#   2) 粤采列表 + 详情 —— 237 个区划切片一次跑不完（实测 20 分钟只到 138 就被掐断），
#      改成按天轮换，每天约 52 个切片，7 天覆盖一轮；
#   3) 正文补采，把新公告的正文 / 结构化信息 / 联系人补齐；
#   4) 分类补录，只算空分类，历史结果不动。
# 每步都会重写 data/tenders.json、data/details/、data/contacts.json、data/status.json，
# 跑完之后还需要调用发布工具把 data 推到线上，前端才会看到新数据。
#
# 用法：
#   bash tools/daily_collect.sh                     # 全流程
#   STAGE=ccgp  bash tools/daily_collect.sh         # 只跑央采（补跑用）
#   STAGE=gdgpo bash tools/daily_collect.sh         # 只跑粤采
#   STAGE=enrich bash tools/daily_collect.sh        # 只跑正文补采 + 分类补录
#   PAGES=1 DETAIL=1 FILL=2 bash tools/daily_collect.sh   # 小规模试跑
#   DRY=1 bash tools/daily_collect.sh               # 只打印命令，不真跑
set -u

PROJ="/Users/kentermacbook/WorkBuddy/2026-10-28-16-13-06/kuai-shangji"
LOGDIR="$PROJ/data/logs"
STAMP=$(date +"%Y-%m-%d")
LOG="$LOGDIR/daily-$STAMP.log"
LOCK="$LOGDIR/daily.lock"

STAGE="${STAGE:-all}"                # all | ccgp | gdgpo | enrich
PAGES="${PAGES:-2}"                  # 每个地区列表翻页数
DETAIL="${DETAIL:-150}"              # 每个源补抓详情条数
FILL="${FILL:-300}"                  # 正文补采条数
GDPARTS="${GDPARTS:-7}"              # 粤采切片几天覆盖一轮
LOCK_MAX_AGE="${LOCK_MAX_AGE:-10800}"  # 锁最长存活 3 小时，超时视为上次被强杀

mkdir -p "$LOGDIR"

# 进程锁：进程还在就直接退出；进程没了、或锁文件超过 3 小时，都当成上次被强杀，清理后继续。
# 定时任务一旦被掐断会留下锁文件，不清理的话后面每天都白跑。
if [ -e "$LOCK" ]; then
  OLD=$(cat "$LOCK" 2>/dev/null | tr -d '[:space:]')
  AGE=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo $(date +%s)) ))
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null && [ "$AGE" -lt "$LOCK_MAX_AGE" ]; then
    echo "[$(date '+%F %T')] 已有采集进程 $OLD 在跑（锁龄 ${AGE}s），本次跳过" >> "$LOG"
    exit 0
  fi
  echo "[$(date '+%F %T')] 清理过期锁（pid=${OLD:-空} 锁龄 ${AGE}s，上次应是被中断）" >> "$LOG"
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

cd "$PROJ" || exit 1

say() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

run() {
  if [ "${DRY:-0}" = "1" ]; then
    say "DRY-RUN: $*"
    return 0
  fi
  "$@" >> "$LOG" 2>&1
  return $?
}

say "===== 每日采集开始 STAGE=$STAGE ====="

if [ "$STAGE" = "all" ] || [ "$STAGE" = "ccgp" ]; then
  say "--- 央采列表 + 详情"
  run python3 -m collector.run --source ccgp --pages "$PAGES" --detail "$DETAIL" --delay 1.1
  say "央采完成 exit=$?"
fi

if [ "$STAGE" = "all" ] || [ "$STAGE" = "gdgpo" ]; then
  GDREG=$(python3 tools/pick_gd_regions.py "$GDPARTS" 2>/dev/null)
  say "--- 粤采列表 + 详情（今日轮换切片 $(echo "$GDREG" | tr ',' '\n' | wc -l | tr -d ' ') 个）"
  run python3 -m collector.run --source gdgpo --regions "$GDREG" \
      --pages "$PAGES" --detail "$DETAIL" --delay 1.1
  say "粤采完成 exit=$?"
fi

if [ "$STAGE" = "all" ] || [ "$STAGE" = "enrich" ]; then
  say "--- 正文补采（新增条目优先）"
  run python3 -m collector.run --fill-content "$FILL" --workers 6 --delay 0.3
  say "正文补采完成 exit=$?"

  say "--- 行业分类补录（只算空分类）"
  run python3 -m collector.run --backfill-category
  say "分类补录完成 exit=$?"
fi

say "===== 采集成品 ====="
python3 - <<'PY' >> "$LOG" 2>&1
import json, os
p = os.path.join("data", "status.json")
st = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
print("SUMMARY " + json.dumps(st, ensure_ascii=False))
PY
grep -h "本次新增" "$LOG" | tail -5 >> "$LOG" 2>/dev/null
say "===== 每日采集结束 ====="
