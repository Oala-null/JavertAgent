#!/usr/bin/env bash
# 院内持续监测入口；仅使用本机已有 Python 与 tmux。
set -euo pipefail
umask 077

root=/home/admin2/Javert
state="$root/output/lis-watch-v1"
py="$root/.venv/bin/python"
session=javert-lis-watch-v1
package=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
    echo '用法：bash watch.sh start [间隔秒数] | status | stop | check'
    echo '默认间隔 300 秒，最小 60 秒。stop 等当前患者结束后退出。'
}

mode=${1:-}
case "$mode" in
    start|__run)
        if [[ $# -gt 2 ]]; then usage; exit 2; fi
        interval=${2:-300}
        if [[ ! "$interval" =~ ^[0-9]{1,9}$ ]]; then
            echo '间隔须为整数秒，至少 60 秒。'; exit 2
        fi
        interval=$((10#$interval))
        if (( interval < 60 )); then
            echo '间隔须至少为 60 秒。'; exit 2
        fi
        ;;
    status|stop|check)
        if [[ $# -ne 1 ]]; then usage; exit 2; fi
        ;;
    *) usage; exit 2 ;;
esac

test -x "$py" || { echo '缺少 /home/admin2/Javert/.venv/bin/python，请核对服务器与应用目录。'; exit 2; }
test -f "$package/launch.py" || { echo '本包缺少 launch.py，请重新核验上传文件。'; exit 2; }
export PYTHONDONTWRITEBYTECODE=1

if [[ "$mode" == status || "$mode" == stop || "$mode" == check ]]; then
    exec "$py" "$package/launch.py" "$mode"
fi

if [[ -L "$state" ]]; then echo '状态目录不能是符号链接。'; exit 2; fi
mkdir -p "$state"
chmod 700 "$state"
if [[ -L "$state/service.log" ]]; then echo '日志文件不能是符号链接。'; exit 2; fi

if [[ "$mode" == __run ]]; then
    cd "$root"
    exec "$py" -u "$package/launch.py" run --interval "$interval" >>"$state/service.log" 2>&1
fi

command -v tmux >/dev/null || { echo '未找到 tmux；请核对院内现有环境。'; exit 2; }
if tmux has-session -t "=$session" 2>/dev/null; then
    echo '监测后台会话已存在，未重复启动。'
    exec "$py" "$package/launch.py" status
fi
tmux new-session -d -s "$session" -c "$root" /bin/bash "$package/watch.sh" __run "$interval"
echo "已提交后台监测：间隔 $interval 秒；会话 $session。"
echo "日志：$state/service.log"
echo '启动是否通过请执行 status；提交会话不代表已完成预检或审计。'
echo "bash '$package/watch.sh' status"
echo '关闭 SSH 后继续；机器重启后需重新 start，原状态保留。'
