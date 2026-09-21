#!/usr/bin/env bash
# 在249执行；无需复制到运行代码目录或重启Web。
set -euo pipefail
umask 077
eda_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd /home/admin2/Javert
export PYTHONDONTWRITEBYTECODE=1
exec /home/admin2/Javert/.venv/bin/python "$eda_dir/eda_249.py" "$@"
