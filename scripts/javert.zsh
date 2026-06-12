# ═══════════════════════════════════════════════════════════════
# Javert onboarding 快捷命令  (在 ~/.zshrc 加一行: source ~/26er/Javert/scripts/javert.zsh)
# ───────────────────────────────────────────────────────────────
# 工作流: /onboarding 拖列映射 → 点「载入数据」(生成 data_import + .loaded.env)
#         → 终端: jv-status 看载入了啥 → jv-run <患者> / jv-run-all 开跑审核
# ═══════════════════════════════════════════════════════════════
# JAVERT_HOME: 显式设了优先; 否则自动探测 (Mac 本地 ~/26er/Javert, 62 部署机 ~/javert)
if [ -z "$JAVERT_HOME" ]; then
  if [ -d "$HOME/26er/Javert" ]; then export JAVERT_HOME="$HOME/26er/Javert"
  elif [ -d "$HOME/javert" ]; then export JAVERT_HOME="$HOME/javert"
  else export JAVERT_HOME="$HOME/26er/Javert"; fi
fi

# 看当前已载入的数据 + 可审核患者列表
jv-status() { ( cd "$JAVERT_HOME" && uv run python scripts/loaded_status.py ); }

# 跑单个患者审核 (实时终端输出)
jv-run() {
  if [ -z "$1" ]; then echo "用法: jv-run <患者号>   (先 jv-status 看有哪些)"; return 1; fi
  ( cd "$JAVERT_HOME" || exit 1
    [ -f data_import/.loaded.env ] && source data_import/.loaded.env
    uv run javert audit-patient "$1" --priority all --use-router --concurrency 5 )
}

# 跑全部已载入患者 (前台, 实时输出)
jv-run-all() { zsh "$JAVERT_HOME/scripts/jv_run_all.sh"; }

# 一词开跑: 网页载入数据后, 终端单敲 jv-go 即跑全部 (redesign D6)
# 在子 shell 里 source .loaded.env (不污染交互 shell — 否则清空数据后 JAVERT_*_FILE 残留报错)
jv-go() {
  if [ ! -f "$JAVERT_HOME/data_import/.loaded.env" ]; then
    echo "⚠ 未找到 data_import/.loaded.env — 请先在网页 /onboarding 点「载入数据」"
    return 1
  fi
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  ( cd "$JAVERT_HOME" && source data_import/.loaded.env && uv run python scripts/loaded_status.py )
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  jv-run-all
}

# 后台跑全部 + 写日志, 然后 jv-watch 看实时进度
jv-run-bg() {
  mkdir -p "$JAVERT_HOME/output"
  nohup zsh "$JAVERT_HOME/scripts/jv_run_all.sh" > "$JAVERT_HOME/output/jv_run.log" 2>&1 &
  echo "后台跑起来了 (PID $!)  →  jv-watch 看实时进度  |  日志: output/jv_run.log"
}

# 实时看后台审核进度
jv-watch() { tail -f "$JAVERT_HOME/output/jv_run.log"; }

# 清空已载入数据 (data_import 的生成 csv + 上传文件 + .loaded.env)
jv-clear() {
  setopt local_options null_glob   # 关键: 未匹配的 glob (如无 stored_*.csv) 不报错中断整条 rm
  local dd="$JAVERT_HOME/data_import"
  echo "将清空 $dd 下: shi_*.csv / case_notes.csv / lab_results.csv / examinations.csv / .loaded.env / 映射 / _uploads/"
  read -q "REPLY?确认? [y/N] " || { echo; return 1; }
  echo
  rm -f "$dd"/shi_*.csv "$dd"/case_notes.csv "$dd"/lab_results.csv "$dd"/examinations.csv \
        "$dd"/.loaded.env "$dd"/column_mapping.generated.yaml "$dd"/_stored_spokes.json "$dd"/stored_*.csv 2>/dev/null
  rm -rf "$dd"/_uploads 2>/dev/null
  echo "✓ 已清空 data_import (下次从 /onboarding 重新载入)"
}

# 起本地工作台 (浏览器开 /onboarding 拖文件)
jv-web() { ( cd "$JAVERT_HOME" && set -a && source .env 2>/dev/null && set +a && uv run javert web --with-mssql --host 127.0.0.1 --port 8090 ); }

# ── aidb SQL 兜底: 一条命令 从 142 aidb 取数 → 跑全部 → 实时上 62 (Mac 便捷别名) ──
# 实体在 scripts/run_aidb_audit.sh (纯 bash; 62 无 zsh, 在 62 直接: bash scripts/run_aidb_audit.sh <名>).
# 与 onboarding/jv-go 互不干扰 (那条本地 sqlite-only; 这条 SQL_ENABLED=true 上 62).
# 注: 为让投资人在 62 看到原文/费用 tab, 在 62 机器上跑 (产出落 62 的 data_import overlay).
jv-aidb() {
  if [ -z "$1" ]; then echo "用法: jv-aidb <投资方名>   (用作批次标签 + 复合患者键前缀)"; return 1; fi
  ( cd "$JAVERT_HOME" && bash scripts/run_aidb_audit.sh "$1" )
}

# 命令速查
jv() {
  cat <<'EOF'
Javert 快捷命令:
  jv-web        起本地工作台 (浏览器开 http://127.0.0.1:8090/onboarding)
  jv-status     看 data_import 已载入了哪些表 + 可审核患者
  jv-go         一词开跑: onboarding 载入数据后单敲此命令即跑全部 (本地 sqlite-only)
  jv-run <ID>   跑单个患者审核 (实时输出)
  jv-run-all    跑全部已载入患者 (前台实时, 逐患者进度行)
  jv-run-bg     后台跑全部 + 写日志
  jv-watch      实时看后台进度 (tail 日志)
  jv-clear      清空已载入数据 (data_import + 上传文件)
  jv-aidb <名>  兜底SQL路: 从 142 aidb 取数 → 跑全部 → 实时上 62 (在 62 机器上跑)
EOF
}
