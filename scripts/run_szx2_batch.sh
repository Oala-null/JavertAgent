#!/usr/bin/env bash
# szx2.0 全量批跑: data_import_hub_szx2 (流B hub 取数) → audit-patient → 实时双写 142 (batch_tag=szx2.0)
# 断点续跑: output/szx2/done.txt 记录已跑患者 (成功/失败都记), 重启自动跳过
# 失败不中断: 失败患者记 failed.txt + 完整日志留 failed_logs/, 批次继续
# ponytail: 串行逐患者 (GPU 单卡饱和, 患者级并发无收益), 规则级 concurrency 5
cd "$(dirname "$0")/.." || exit 1

export JAVERT_DATA_DIR=data_import_hub_szx2
export JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv
export JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv
export JAVERT_SQL_ENABLED=true
export JAVERT_BATCH_TAG=szx2.1

OUT=output/szx2
mkdir -p "$OUT/failed_logs"
touch "$OUT/done.txt"

n=$(grep -c . data/szx2_patients.txt)
i=0
while IFS= read -r p; do
  [ -z "$p" ] && continue
  i=$((i + 1))
  grep -qxF "$p" "$OUT/done.txt" && continue
  tmp=$(mktemp)
  uv run javert audit-patient "$p" --priority all --use-router --concurrency 5 > "$tmp" 2>&1
  rc=$?
  line=$(grep -oE 'Verdicts: V=[0-9]+ / C=[0-9]+ / I=[0-9]+' "$tmp" | head -1)
  ts=$(date '+%m-%d %H:%M:%S')
  if [ $rc -ne 0 ] || [ -z "$line" ]; then
    echo "$p" >> "$OUT/failed.txt"
    cp "$tmp" "$OUT/failed_logs/$p.log"
    echo "[$i/$n] $ts $p FAIL rc=$rc" >> "$OUT/progress.log"
  else
    echo "[$i/$n] $ts $p OK $line" >> "$OUT/progress.log"
  fi
  echo "$p" >> "$OUT/done.txt"
  rm -f "$tmp"
done < data/szx2_patients.txt
echo "ALL-DONE $(date '+%m-%d %H:%M:%S')" >> "$OUT/progress.log"
