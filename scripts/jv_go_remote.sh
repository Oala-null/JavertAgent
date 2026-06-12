#!/usr/bin/env bash
# 62 (bash, 无 zsh) 上跑 onboarding 载入数据的全部患者审核.
# 与 zsh 版 jv_run_all.sh 等价, 但纯 bash —— Mac 的 jv-go-62 / 62 的 jvgo 调它.
# 源 data_import/.loaded.env (含 JAVERT_DATA_DIR/_FILE + SQL_ENABLED=true + BATCH_TAG);
# 142 连接用 config 默认 (192.168.31.142). 单患者失败不中断.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"   # 非交互 ssh 下保证 uv 在 PATH
cd "${JAVERT_HOME:-$HOME/javert}" || { echo "找不到 javert 目录"; exit 1; }
[ -f data_import/.loaded.env ] && source data_import/.loaded.env

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
uv run python scripts/loaded_status.py
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ids="$(uv run python scripts/loaded_status.py --ids)"
if [ -z "${ids//[$'\n\r ']/}" ]; then
  echo "⚠ 没有可审核患者 — 先在 /onboarding 点「载入数据」"
  exit 1
fi
n=$(printf '%s\n' "$ids" | grep -c .)
echo "▶ 共 $n 个患者, 开始逐个审核 (单个失败不中断)…"
i=0; ok=0; fail=0; tmp=$(mktemp)
while IFS= read -r p; do
  [ -z "$p" ] && continue
  i=$((i + 1))
  echo ""; echo "══════════ [$i/$n] ▶ $p ══════════"
  uv run javert audit-patient "$p" --priority all --use-router --concurrency 5 2>&1 | tee "$tmp"
  rc=${PIPESTATUS[0]}
  line=$(grep -oE 'Verdicts: V=[0-9]+ / C=[0-9]+ / I=[0-9]+' "$tmp" | head -1)
  if [ "$rc" -ne 0 ] || [ -z "$line" ]; then
    echo "[$i/$n] $p ✗ 审核失败 (rc=$rc) — 跳过, 继续"
    fail=$((fail + 1))
  else
    v=$(printf '%s' "$line" | grep -oE 'V=[0-9]+' | grep -oE '[0-9]+')
    cc=$(printf '%s' "$line" | grep -oE 'C=[0-9]+' | grep -oE '[0-9]+')
    ii=$(printf '%s' "$line" | grep -oE 'I=[0-9]+' | grep -oE '[0-9]+')
    echo "[$i/$n] $p ✓ ${v}V ${ii}I ${cc}C"
    ok=$((ok + 1))
  fi
done <<< "$ids"
rm -f "$tmp"
echo ""; echo "✅ 全部跑完: $n 个患者 ($ok 成功 / $fail 失败)"
