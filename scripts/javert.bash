# ═══════════════════════════════════════════════════════════════
# Javert 快捷命令 — bash 版 (62 部署机无 zsh, 用这个)
#   在 ~/.bashrc 加一行:  source ~/javert/scripts/javert.bash
#   (Mac 本地用 zsh 的 scripts/javert.zsh; 二者功能对齐)
# ═══════════════════════════════════════════════════════════════

# JAVERT_HOME 自动探测 (Mac ~/26er/Javert / 62 ~/javert); 显式设了优先
if [ -z "$JAVERT_HOME" ]; then
  if [ -d "$HOME/26er/Javert" ]; then export JAVERT_HOME="$HOME/26er/Javert"
  elif [ -d "$HOME/javert" ]; then export JAVERT_HOME="$HOME/javert"
  else export JAVERT_HOME="$HOME/26er/Javert"; fi
fi
_jv_uv() { command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv"; }

# ── 看 data_import 已载入的表 + 可审核患者 ──
jv-status() { ( cd "$JAVERT_HOME" && "$(_jv_uv)" run python scripts/loaded_status.py ); }

# ── aidb SQL 兜底一键: 142 aidb 取数 → 跑全部 → 实时上 62 (主力命令) ──
jv-aidb() {
  if [ -z "$1" ]; then echo "用法: jv-aidb <投资方名>   (用作批次标签 + 复合患者键前缀)"; return 1; fi
  ( cd "$JAVERT_HOME" && bash scripts/run_aidb_audit.sh "$1" )
}

# ── aidb 兜底后台版 + 看进度 (患者多/跑得久时用) ──
jv-aidb-bg() {
  if [ -z "$1" ]; then echo "用法: jv-aidb-bg <投资方名>"; return 1; fi
  mkdir -p "$JAVERT_HOME/output"
  ( cd "$JAVERT_HOME" && nohup bash scripts/run_aidb_audit.sh "$1" > output/aidb_run.log 2>&1 & )
  echo "后台跑起来了 → jv-watch 看实时进度 | 日志: $JAVERT_HOME/output/aidb_run.log"
}
jv-watch() { tail -f "$JAVERT_HOME/output/aidb_run.log"; }

# ── onboarding 本地路 (sqlite-only, 不入 142/工作台): 跑单个 / 全部 ──
jv-run() {
  if [ -z "$1" ]; then echo "用法: jv-run <患者号>   (先 jv-status 看有哪些)"; return 1; fi
  ( cd "$JAVERT_HOME" || return 1
    [ -f data_import/.loaded.env ] && source data_import/.loaded.env
    "$(_jv_uv)" run javert audit-patient "$1" --priority all --use-router --concurrency 5 )
}
jv-run-all() {
  ( cd "$JAVERT_HOME" || return 1
    [ -f data_import/.loaded.env ] && source data_import/.loaded.env
    local uv ids n i=0; uv="$(_jv_uv)"
    ids="$("$uv" run python scripts/loaded_status.py --ids)"
    if [ -z "$ids" ]; then echo "⚠ 没有可审核患者 — 先在 /onboarding 载入数据"; return 1; fi
    n=$(printf '%s\n' "$ids" | grep -c .)
    echo "▶ 共 $n 个患者, 逐个审核 (单个失败不中断)…"
    for p in $ids; do
      i=$((i + 1)); echo ""; echo "═══════ [$i/$n] ▶ $p ═══════"
      "$uv" run javert audit-patient "$p" --priority all --use-router --concurrency 5 \
        || echo "[$i/$n] $p ✗ 审核失败 — 已跳过, 继续"
    done
    echo ""; echo "✅ 全部跑完 ($n 个)" )
}

# ── 命令速查 ──
jv() {
  cat <<'EOF'
Javert 快捷命令 (bash):
  jv-status        看 data_import 已载入的表 + 可审核患者
  jv-aidb   <名>   ★兜底SQL路: 142 aidb 取数 → 跑全部 → 实时上 62 (跑完按提示重启 javert-web)
  jv-aidb-bg <名>  同上但后台跑 + 写日志 (患者多时用)
  jv-watch         tail aidb 后台日志看进度
  jv-run    <ID>   跑单个患者 (onboarding 本地 sqlite-only, 不入工作台)
  jv-run-all       跑全部已载入患者 (onboarding 本地 sqlite-only)
EOF
}
