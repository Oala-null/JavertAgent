#!/usr/bin/env bash
# 先按现机方式停止旧Web，再运行本启动器；不启动第二个相同端口实例。
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
export JAVERT_HUB_LINKAGE_MODE=shanghai
export JAVERT_HUB_HOSPITAL_CODE="${JAVERT_HUB_HOSPITAL_CODE:-AYY8BNRF}"
export JAVERT_HUB_RAW_ENABLED=true
"$PWD/.venv/bin/python" scripts/check_243_runtime.py --web
exec "$PWD/.venv/bin/python" -m javert.cli web --with-mssql --host 0.0.0.0 --port 8090
