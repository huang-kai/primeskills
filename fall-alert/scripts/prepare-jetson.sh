#!/usr/bin/env bash
# Run on SoC1. Reuse the firmware CUDA/ROS packages through system site-packages.
set -eo pipefail
umask 077
env_dir=$1
config=$2
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
py="$env_dir/bin/python"
fail() { echo "$1" >&2; exit 1; }

test -s /robot/software/algorithm/models/yolo26n-seg_640x480_fp16_nx.engine || fail '缺少 T1 配套的 YOLO TensorRT 引擎，请查看 docs/installation.md。'
command -v ffmpeg >/dev/null || fail 'Jetson 缺少 FFmpeg，请查看 docs/installation.md。'
ffmpeg -hide_banner -encoders 2>/dev/null | grep ' libx264 ' >/dev/null || fail 'Jetson 的 FFmpeg 缺少 libx264 编码器。'
source /opt/ros/humble/setup.bash
source /robot/software/interface/setup.bash

if [[ ! -x $py || ! -f $env_dir/pyvenv.cfg ]]; then
    if /usr/bin/python3 -c 'import pip' 2>/dev/null; then
        /usr/bin/python3 -m venv --system-site-packages --without-pip "$env_dir" || fail '创建环境失败，请安装 python3-venv 后重试。'
    else
        /usr/bin/python3 -m venv --system-site-packages "$env_dir" || fail '创建环境失败，请安装 python3-venv 后重试。'
    fi
fi
"$py" -c 'import sys; from pathlib import Path; assert sys.prefix != sys.base_prefix and Path(sys.prefix).samefile(sys.argv[1]), "无效的项目虚拟环境"' "$env_dir"
if ! "$py" -m pip --version >/dev/null 2>&1; then
    "$py" -m ensurepip || fail '缺少 pip，请按 docs/installation.md 补齐 python3-venv 后重试。'
fi
"$py" -m pip --disable-pip-version-check install -r "$root/requirements.txt"
exec "$py" -B "$root/scripts/install_runtime.py" "$config" "${@:3}"
