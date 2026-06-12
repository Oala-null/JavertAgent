#!/usr/bin/env bash
# aidb SQL 兜底: 一条命令 从 142 aidb 取数 → 跑全部患者 → 实时上 62 工作台.
# 纯 bash (62 无 zsh 也能跑). Mac 上 jv-aidb 委托本脚本; 62 上直接:
#   cd ~/javert && bash scripts/run_aidb_audit.sh <投资方名>
#
# 前提: 工程师已把数据填进 142 aidb 的 6 张 intake_* 表 (建表见 scripts/sql/create_aidb_tables.sql).
# 与 onboarding/jv-go 互不干扰 (那条本地 sqlite-only; 这条 SQL_ENABLED=true 上 62, 不读 .loaded.env).
# 为让投资人在 62 看到原文/费用 tab, 在 62 机器上跑 (产出落 62 的 data_import overlay).
# 刻意不 set -e — 单个患者失败不中断整批.

TAG="$1"
if [ -z "$TAG" ]; then
  echo "用法: bash scripts/run_aidb_audit.sh <投资方名>   (用作批次标签 + 复合患者键前缀)"
  exit 1
fi

# 项目根 = 本脚本上一级目录 (Mac ~/26er/Javert / 62 ~/javert 自适应)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
# uv 路径 (62 非交互 shell PATH 可能没有)
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

echo "━━━ ① 从 142 aidb 拉数 + 连接预检 (批次: $TAG) ━━━"
"$UV" run python scripts/etl_from_sql.py --hospital-code "$TAG" --output data_import || {
  echo "✗ 拉数失败 (142 不可达 / aidb 6 表空 / 预检🔴) — 中止; 让工程师核对各表「住院号」是否一致"
  exit 1
}

export JAVERT_DATA_DIR=data_import JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv
export JAVERT_SQL_ENABLED=true JAVERT_BATCH_TAG="$TAG"
[ -f data_import/lab_results.csv ] && export JAVERT_LABS_FILE=lab_results.csv
[ -f data_import/examinations.csv ] && export JAVERT_EXAMINATIONS_FILE=examinations.csv

ids="$("$UV" run python scripts/loaded_status.py --ids)"
if [ -z "$ids" ]; then
  echo "✗ 没有可审核患者 (费用∩文书为空) — 检查 aidb 数据"
  exit 1
fi
n="$(printf '%s\n' "$ids" | grep -c .)"

echo ""
echo "━━━ ② 跑全部患者 (结果实时双写 142 → 62 工作台): 共 $n 个 ━━━"
i=0; ok=0; fail=0
for pid in $ids; do
  i=$((i + 1))
  echo ""
  echo "═══════ [$i/$n] ▶ $pid ═══════"
  if "$UV" run javert audit-patient "$pid" --priority all --use-router --concurrency 5; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
    echo "[$i/$n] $pid ✗ 审核失败 — 已跳过, 继续下一个"
  fi
done

echo ""
echo "✅ 全部跑完: $n 个患者 ($ok 成功 / $fail 失败)"
echo ""
echo "⚠ 若在 62 机器上跑: 违规列表+裁决+证据已实时显示; 但新患者的【原文/费用 tab + 概览】"
echo "  走 web 进程内缓存, 需重启工作台一次才完整加载 (审核结果在 142, 重启不丢):"
echo "    sudo systemctl restart javert-web"
echo "→ 然后打开 http://192.168.31.62:8090 → 左侧按批次「$TAG」筛选, 只看这批新数据的违规"
