#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
here=$(cd "$(dirname "$0")" && pwd)
exec /home/admin2/Javert/.venv/bin/python "$here/installer.py" "$@"
