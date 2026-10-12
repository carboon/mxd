#!/bin/bash
# 冒险岛·石人寺院II 复刻 demo 启动脚本
cd "$(dirname "$0")"

PY=""
if [ -x "$HOME/miniconda3/envs/mxd/bin/python" ]; then
  PY="$HOME/miniconda3/envs/mxd/bin/python"
elif command -v conda >/dev/null 2>&1; then
  source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null
  conda activate mxd 2>/dev/null && PY="$(command -v python)"
fi
[ -z "$PY" ] && PY="$(command -v python3)"

echo "using python: $PY"
"$PY" -c "import pygame" 2>/dev/null || "$PY" -m pip install pygame
exec "$PY" game/main.py "$@"
