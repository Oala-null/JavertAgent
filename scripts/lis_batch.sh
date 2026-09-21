#!/usr/bin/env bash
# 用法：bash scripts/lis_batch.sh start [本次审计上限，缺省全部]
#       bash scripts/lis_batch.sh status
#       bash scripts/lis_batch.sh resume latest [本次上限]
#       多个未完成批次时，把latest换成明确的output/lis-batch.XXXX目录。
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
live="$PWD"
py="$live/.venv/bin/python"
action="${1:-status}"
if [[ "$action" == status ]]; then
    exec "$py" scripts/run_243_lis_batch.py status
fi
case "$action" in
  start) args=(new); limit="${2:-0}"; test "$#" -le 2 ;;
  resume) test -n "${2:-}" || { echo 'resume需要批次目录'; exit 2; }; args=(resume "$2"); limit="${3:-0}"; test "$#" -le 3 ;;
  *) echo '用法：start [上限] | status | resume 批次目录 [上限]'; exit 2 ;;
esac
[[ "$limit" =~ ^[0-9]{1,6}$ ]] || { echo '上限须为整数，0表示全部'; exit 2; }
command -v tmux >/dev/null || { echo '缺少tmux'; exit 1; }
mkdir -p output
flock -n output/.249-batch.lock true || { echo '已有批跑，先查看状态'; exit 1; }
flock -n output/.243-patient.lock true || { echo '已有单例任务，先等待完成'; exit 1; }
folder=$(mktemp -d "$live/output/lis-launch.XXXXXX")
cat > "$folder/run.sh" <<'RUN'
#!/usr/bin/env bash
set -euo pipefail
umask 077
log_dir="$1"
shift
exec >"$log_dir/launch.log" 2>&1
if "$@"; then rc=0; else rc=$?; fi
printf '%s\n' "$rc" > "$log_dir/exit-code.txt"
exit "$rc"
RUN
session="javert-lis-${folder##*.}"
tmux new-session -d -s "$session" -c "$live" /bin/bash "$folder/run.sh" "$folder" \
    "$py" "$live/scripts/run_243_lis_batch.py" "${args[@]}" --max-audits "$limit"
printf '已提交后台任务\n会话=%s\n启动日志=%s/launch.log\n' "$session" "$folder"
echo '查看批次进度：bash /home/admin2/Javert/scripts/lis_batch.sh status'
sleep 1
test ! -f "$folder/launch.log" || tail -n 10 "$folder/launch.log"
