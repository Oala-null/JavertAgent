#!/usr/bin/env zsh
# 在 /onboarding 载入的 data_import 数据上, 逐个跑全部可审核患者.
# 逐患者打印进度行 [i/N] 患者号 ✓ xV yI zC; 单患者失败标 ✗ 不中断 (redesign D6).
# jv-go / jv-run-all 前台调它; jv-run-bg 用 nohup 后台调它写日志.
# 刻意不 set -e — 一个患者失败不能让整批中断.
cd "${JAVERT_HOME:-$HOME/26er/Javert}" || exit 1
[ -f data_import/.loaded.env ] && source data_import/.loaded.env

# 健壮性: 已 set 的 JAVERT_*_FILE 指向不存在文件 → WARN 跳过 (不 ERROR, redesign 3.2)
dd="${JAVERT_DATA_DIR:-data_import}"
for var in JAVERT_FEES_FILE JAVERT_NOTES_FILE JAVERT_ZD_FILE JAVERT_SS_FILE \
           JAVERT_LABS_FILE JAVERT_EXAMINATIONS_FILE; do
  val=${(P)var}
  if [ -n "$val" ] && [ ! -f "$dd/$val" ]; then
    print "⚠ $var=$val 指向的文件不存在 ($dd/$val) — 跳过该数据源"
  fi
done

ids=("${(@f)$(uv run python scripts/loaded_status.py --ids)}")
ids=("${(@)ids:#}")  # 去空行
if [ ${#ids[@]} -eq 0 ]; then
  echo "⚠ 没有可审核患者 — 先在 /onboarding 点「载入数据」"
  exit 1
fi

n=${#ids[@]}
echo "▶ 共 $n 个患者, 开始逐个审核 (单个失败不中断)…"
ok=0; fail=0; i=0
tmp=$(mktemp)
for p in "${ids[@]}"; do
  [ -z "$p" ] && continue
  i=$((i + 1))
  print "\n══════════ [$i/$n] ▶ $p ══════════"
  # tee: 实时滚动真实审计日志 (技术买家看着在跑) + 落 tmp 供解析 Verdicts 行
  uv run javert audit-patient "$p" --priority all --use-router --concurrency 5 2>&1 | tee "$tmp"
  rc=${pipestatus[1]}
  line=$(grep -oE 'Verdicts: V=[0-9]+ / C=[0-9]+ / I=[0-9]+' "$tmp" | head -1)
  if [ $rc -ne 0 ] || [ -z "$line" ]; then
    print "[$i/$n] $p ✗ 审核失败 (rc=$rc) — 已跳过, 继续下一个"
    fail=$((fail + 1))
  else
    v=$(print "$line" | grep -oE 'V=[0-9]+' | grep -oE '[0-9]+')
    cc=$(print "$line" | grep -oE 'C=[0-9]+' | grep -oE '[0-9]+')
    ii=$(print "$line" | grep -oE 'I=[0-9]+' | grep -oE '[0-9]+')
    print "[$i/$n] $p ✓ ${v}V ${ii}I ${cc}C"
    ok=$((ok + 1))
  fi
done
rm -f "$tmp"
echo "\n✅ 全部跑完: $n 个患者 ($ok 成功 / $fail 失败)"
