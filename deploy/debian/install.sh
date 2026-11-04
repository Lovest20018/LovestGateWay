#!/bin/sh
set -eu
command -v python3 >/dev/null 2>&1 || { echo '请先安装 python3（Debian 12 或更新版本）。' >&2; exit 1; }
PPGW_PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -B -X utf8 "$PPGW_PACKAGE_DIR/deploy.py" "$@"
