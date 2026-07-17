#!/usr/bin/env bash
# med_rst 批跑: RD04 (肿瘤药医保限定专项) × 预扫命中患者.
# 前置: uv run python scripts/prescan_med_rst.py 产出 output/med_rst_patients.json
# 跑法: nohup bash scripts/run_med_rst_batch.sh > output/med_rst_batch/_main.log 2>&1 &
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
export JAVERT_BATCH_TAG="${JAVERT_BATCH_TAG:-med_rst}"
# 默认保留系统 drift guard。确需关闭时由操作者显式传入
# `JAVERT_DRIFT_GUARD=off bash scripts/run_med_rst_batch.sh`，脚本不再静默绕过。

mkdir -p output/med_rst_batch
python3 -c "import json; [print(p) for p in json.load(open('output/med_rst_patients.json'))]" \
  > output/med_rst_batch/patients.txt
total=$(wc -l < output/med_rst_batch/patients.txt | tr -d ' ')
echo "[$(date '+%F %T')] med_rst 批跑开始: ${total} 患者 × RD04, 并发 3, tag=${JAVERT_BATCH_TAG}"

# ponytail: xargs -P 3 简单并发, 单患者失败不中断; 更精细的进度/重试用 jv_run_all.sh 模式
xargs -P 3 -I{} bash -c '
  pid="{}"
  if uv run javert audit-patient "$pid" --rules RD04 > "output/med_rst_batch/${pid}.log" 2>&1; then
    echo "[$(date "+%T")] ✓ ${pid}"
  else
    echo "[$(date "+%T")] ✗ ${pid} FAILED (见 output/med_rst_batch/${pid}.log)"
  fi
' < output/med_rst_batch/patients.txt

echo "[$(date '+%F %T')] med_rst 批跑结束"
