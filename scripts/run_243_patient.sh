#!/usr/bin/env bash
# 单例先预检再审计；原.env/进程环境继续生效，不需重复输入SQL密码。
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
export JAVERT_HUB_LINKAGE_MODE=shanghai
export JAVERT_HUB_HOSPITAL_CODE="${JAVERT_HUB_HOSPITAL_CODE:-AYY8BNRF}"
py="$PWD/.venv/bin/python"
test -x "$py" || { echo '缺少本机.venv；不要从Mac复制虚拟环境'; exit 2; }
patient="${1:-}"
[[ "$patient" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || { echo '用法：bash scripts/run_243_patient.sh <首页SYXH> [tag] [--check-only]'; exit 2; }
tag="${2:-one-$(date +%y%m%d%H%M%S)}"
[[ "$tag" =~ ^[A-Za-z0-9_-]{1,20}$ ]] || { echo 'tag须为1-20位ASCII字母数字、_或-'; exit 2; }
if [[ "${3:-}" == --check-only ]]; then
    exec "$py" scripts/etl_from_data_hub.py --patients "$patient" --check-only
fi
[[ -z "${3:-}" ]] || { echo '未知第三参数'; exit 2; }
"$py" scripts/check_243_runtime.py
mkdir -p output
run_dir=$(mktemp -d "$PWD/output/patient.XXXXXX")
echo "本次目录：$run_dir；批次tag：$tag"
exec 9>"$PWD/output/.243-patient.lock"
flock -n 9 || { echo '另一个单例任务正在运行'; exit 2; }
"$py" scripts/etl_from_data_hub.py --patients "$patient" --output "$run_dir/data" \
    2>&1 | tee "$run_dir/etl.log"
export JAVERT_DATA_DIR="$run_dir/data"
export JAVERT_NOTES_FILE=case_notes.csv JAVERT_FEES_FILE=shi_fee.csv
export JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv
export JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv
export JAVERT_BATCH_TAG="$tag"
echo '[审计] 仅使用上一步快照，concurrency=1；是否SQL双写沿用现场配置。'
"$py" -m javert.cli audit-patient "$patient" --priority all --use-router --concurrency 1 \
    2>&1 | tee "$run_dir/audit.log"
echo '命令已结束：请核对audit.log的failed、mssql_sync/pending，以及工作台同tag结果。'
echo "原文/日志含敏感信息，保留院内：$run_dir"
