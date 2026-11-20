#!/usr/bin/env bash
# Jetson local detector and SMS worker. Configuration never crosses the board link.
set -eo pipefail
source /opt/ros/humble/setup.bash
source /robot/software/interface/setup.bash
profile=/robot/software/entry/cfg/standard/ros_dds_configuration_all.xml
if [[ -r $profile ]]; then export FASTRTPS_DEFAULT_PROFILES_FILE="${FASTRTPS_DEFAULT_PROFILES_FILE:-$profile}"; fi
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ${1:-} == restore-voice ]]; then
    exec /usr/bin/python3 -Bu "$root/src/jetson/graphical_voice.py" "$2"
fi
exec /robot/persist/t1-fall-alert/venv/bin/python -Bu "$root/src/jetson/send.py" --case-once --config-stdin < "$root/cfg/config.yaml"
