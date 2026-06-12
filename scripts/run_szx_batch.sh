#!/usr/bin/env bash
# szx 外部数据 5 患者批跑.
# 用法: bash scripts/run_szx_batch.sh

set -e

export JAVERT_DATA_DIR=data_import
export JAVERT_ZD_FILE=shi_zd.csv
export JAVERT_SS_FILE=shi_ss.csv
export JAVERT_SQL_ENABLED=false
export JAVERT_BATCH_TAG=szx

PATIENTS=(211454284 211486870 211500689 211518131 211548244)

mkdir -p output/szx_batch

for pid in "${PATIENTS[@]}"; do
    echo ""
    echo "═══════════════════════════════════════════════"
    echo "开始审计 $pid  $(date '+%H:%M:%S')"
    echo "═══════════════════════════════════════════════"
    log="output/szx_batch/${pid}.log"
    uv run javert audit-patient "$pid" --priority all --use-router --concurrency 5 \
        > "$log" 2>&1 || echo "  ✗ $pid 失败 (查看 $log)"
    tail -15 "$log"
done

echo ""
echo "═══════════════════════════════════════════════"
echo "全部完成 $(date '+%H:%M:%S')"
echo "═══════════════════════════════════════════════"
