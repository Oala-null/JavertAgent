#!/bin/bash
# deploy_drug_audit.sh — 在 192.168.31.62 上线 add-drug-audit-rules (v0.8) 的非 sudo 部分.
# 用法 (在 62 上, 从 ~/javert):  bash /tmp/deploy_drug_audit.sh
# 前置: 已把 /tmp/javert-drug-audit-deploy.tgz scp 到 62:/tmp/
# 重启 (需 sudo 密码) 由调用方单独执行: sudo systemctl restart javert-web
set -euo pipefail
cd "$HOME/javert"
UV="$HOME/.local/bin/uv"
BUNDLE="/tmp/javert-drug-audit-deploy.tgz"

echo "[1/5] 备份当前 src + 药品相关 configs (回滚用)"
ts=$(date +%Y%m%d-%H%M%S)
tar -czf "$HOME/javert-backup-pre-drugaudit-$ts.tgz" \
    --exclude='__pycache__' src configs/rules configs/templates configs/rule_mapping.json data/router/javert_rules_index.json 2>/dev/null || true
echo "      backup → $HOME/javert-backup-pre-drugaudit-$ts.tgz"
echo "$ts" > /tmp/javert-drugaudit-backup-ts.txt

echo "[2/5] 解包 bundle (src + M8 + drug_audit_kb.json + R007/RD* + scripts + 药品 xlsx)"
test -f "$BUNDLE" || { echo "✗ 缺 $BUNDLE, 先 scp 过来"; exit 1; }
tar xzf "$BUNDLE"
find . -name '._*' -delete 2>/dev/null || true

echo "[3/5] 加载 .env + 用 62 实际 yaml 重建 router index (比直接用打包 index 更安全)"
set -a; source .env; set +a
"$UV" run python scripts/build_rule_mapping.py | tail -3

echo "[4/5] 校验: M8 模板 ready + 药品规则可加载"
"$UV" run javert template validate M8
echo -n "      drug rules (R007 + RD*) ready: "
"$UV" run javert list | grep -E '^(R007|RD[0-9])' | grep -c ready
echo "      registry 总览:"; "$UV" run javert list | tail -1

echo "[5/5] ✓ 代码/配置已就位 (服务尚未重启)."
echo "      下一步 (需 sudo 密码, 调用方执行): sudo systemctl restart javert-web"
echo "      回滚: tar xzf \$HOME/javert-backup-pre-drugaudit-$ts.tgz && sudo systemctl restart javert-web"
