#!/usr/bin/env bash
# Javert 243/249 Workbench 后台启动
# 上传到 /home/admin2/start_javert_web_bg.sh 后执行：
# bash /home/admin2/start_javert_web_bg.sh
# 复用 /home/admin2/releases/243-install-8645a2d.* 中保存的配置。
# 关闭 SSH 后继续运行；本脚本不配置机器开机自启动。

set -euo pipefail
umask 077
live='/home/admin2/Javert'
test "$(head -1 "$live/DEPLOY_COMMIT")" = '8645a2deb1c4c99921a65a3ab01754275ec2ac4e' || { echo '版本不同，停止。'; exit 1; }
command -v tmux >/dev/null || { echo '未安装tmux，停止。'; exit 1; }
if ss -lntH 'sport = :8090' | grep -q .; then
    echo '8090已有服务，不重复启动。登录页HTTP状态：'
    curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:8090/login
    exit 0
fi
shopt -s nullglob
helpers=()
for f in /home/admin2/releases/243-install-8645a2d.*/run_saved_config.py; do
    test -f "${f%/*}/install-receipt.json" && helpers+=("$f")
done
test "${#helpers[@]}" -eq 1 || { echo '安装配置不是唯一一份，停止。'; exit 1; }
if tmux has-session -t '=javert-web-bg' 2>/dev/null; then
    echo '后台Web会话已存在但端口未就绪，先检查其启动日志，不重复启动。'
    exit 1
fi
mkdir -p "$live/output"
ops=$(mktemp -d "$live/output/web-bg.XXXXXX")
cat > "$ops/start.sh" <<'WEB'
#!/usr/bin/env bash
set -euo pipefail
umask 077
exec >"$2" 2>&1
exec python3 "$1" web
WEB
tmux new-session -d -s javert-web-bg -c "$live" /bin/bash "$ops/start.sh" "${helpers[0]}" "$ops/web.log"
echo "已提交后台启动；日志仅在院内：$ops/web.log"
for i in {1..15}; do
    code=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8090/login || true)
    if [[ "$code" == 200 ]]; then
        echo 'Web登录页HTTP 200。可以关闭SSH窗口；访问 http://192.168.60.249:8090/workbench'
        exit 0
    fi
    sleep 1
done
echo 'Web尚未通过健康检查，请在院内查看上述日志；不要重复启动。'
exit 1
