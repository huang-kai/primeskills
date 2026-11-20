"""Run one T1 fall-alert case on Jetson, with an optional control-board display."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src/jetson'))
from settings import parse, read_private, write_private
from graphical_voice import owner as voice_owner

CONFIG = ROOT / 'cfg/config.yaml'
EXAMPLE = ROOT / 'cfg/config.example.yaml'
BASE = Path('/robot/persist/t1-fall-alert')
WATCH = 't1-fall-skill-watch.service'
DISPLAY = 't1-fall-skill-display.service'
SSH = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
       'run@10.1.1.100']
IDLE = {'inactive', 'failed', 'unknown'}


def check_board():
    info = Path('/robot/data/info')
    if (info / 'series').read_text().strip() != 'T1' or (info / 'soc_index').read_text().strip() != '1':
        raise RuntimeError('请在 T1 大脑板 Jetson（SoC1）上运行。')


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def display_payload():
    files = [ROOT / 'src/control' / name for name in ('watch_display.py', 'receive.py')]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return files, BASE / 'display' / digest.hexdigest()


def install(import_legacy=False):
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (BASE / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not CONFIG.exists():
            write_private(CONFIG, EXAMPLE.read_text())
        CONFIG.chmod(0o600)
        blank = read_private(CONFIG).strip() == EXAMPLE.read_text().strip()
        args = ['bash', str(ROOT / 'scripts/prepare-jetson.sh'), str(BASE / 'venv'), str(CONFIG)]
        if import_legacy:
            if not blank:
                raise ValueError('cfg/config.yaml 已有修改，保留现有文件。请手动编辑。')
            legacy = BASE / 'private/config.json'
            args.append(str(legacy if legacy.is_file() else BASE / 'private/sms.json'))
        command(args)
        install_display()
    if blank and not import_legacy:
        return '两块板安装完成。请在 Jetson 填写 cfg/config.yaml，然后运行 python3 main.py start。'
    return '两块板安装完成。运行 python3 main.py start 开始一轮看护。'


def install_display():
    files, release = display_payload()
    archive_data = io.BytesIO()
    with tarfile.open(fileobj=archive_data, mode='w:gz') as archive:
        for path in files:
            archive.add(path, arcname=path.name)
    install = f'''set -e
test "$(cat /robot/data/info/series)" = T1
test "$(cat /robot/data/info/soc_index)" = 0
source /opt/ros/humble/setup.bash
source /robot/software/interface/setup.bash
command -v ffmpeg >/dev/null || {{ echo "运控板缺少 FFmpeg，请参阅 docs/installation.md" >&2; exit 1; }}
/usr/bin/python3 - <<'PYTHON'
import gi, rclpy
from std_msgs.msg import Bool
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
for element in ('kmssink', 'appsrc', 'queue'):
    if Gst.ElementFactory.find(element) is None:
        raise SystemExit('运控板缺少 GStreamer 插件 ' + element)
PYTHON
umask 077
mkdir -p {release.parent}
exec 9>{release.parent}/install.lock
flock -x 9
stage=$(mktemp -d {release.parent}/.install-XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
tar -xz -C "$stage"
if test ! -d {release}; then mv -T "$stage" {release}; fi
'''
    try:
        command(SSH + ['bash -c ' + shlex.quote(install)], input=archive_data.getvalue(), timeout=45)
    except subprocess.SubprocessError:
        raise RuntimeError('运控板显示组件安装失败，请检查上方提示和 docs/installation.md 后重试 install。') from None


def unit_state(unit, remote=False):
    args = ['systemctl', 'is-active', unit]
    result = subprocess.run(SSH + [shlex.join(args)] if remote else args, capture_output=True,
                            text=True, timeout=18)
    value = result.stdout.strip()
    if result.returncode not in (0, 3, 4) or value not in IDLE | {'active', 'activating', 'deactivating', 'reloading'}:
        raise RuntimeError(f'无法读取 {unit} 状态。')
    return value


def status():
    jetson = unit_state(WATCH)
    control = unit_state(DISPLAY, remote=True)
    ui = unit_state('display_ui.service', remote=True)
    marker = command(SSH + ['if test -e /run/t1-fall-skill-display/ui-owned; then echo owned; else echo free; fi'],
                     capture_output=True, text=True, timeout=10).stdout.strip()
    voice = voice_owner()
    released = jetson in IDLE and control in IDLE and ui == 'active' and marker == 'free' and not voice
    return dict(status='success', message='已退出并恢复表情服务。' if released else '运行中或正在恢复。',
                resources_released=released, jetson=jetson, control=control, factory_ui=ui,
                voice_session=voice)


def session_tag():
    tag = os.environ.get('T1_FALL_SESSION', '')
    if not tag.startswith('t1-fall-') or len(tag) != 40 or any(c not in '0123456789abcdef' for c in tag[8:]):
        raise RuntimeError('缺少本轮临时服务标识。')
    return tag


def run_case():
    tag = session_tag()
    _, release = display_payload()
    watcher = str(release / 'watch_display.py')
    player = ['sudo', '-n', 'systemd-run', '--collect', '--unit=' + DISPLAY, '--description=' + tag,
              '--uid=run', '--gid=run', '--property=KillMode=mixed', '--property=TimeoutStopSec=22',
              '--property=RuntimeDirectory=t1-fall-skill-display', '--property=RuntimeDirectoryPreserve=yes',
              '--property=RuntimeDirectoryMode=0700', '--setenv=T1_FALL_CASE=1',
              '--property=ExecStopPost=/usr/bin/python3 -Bu ' + watcher + ' --restore',
              '/bin/bash', '-c', 'source /opt/ros/humble/setup.bash && source /robot/software/interface/setup.bash'
              ' && exec /usr/bin/python3 -Bu ' + watcher + ' --case-once']
    # No preview takeover if an older display session is already using the screen.
    remote = ('set -eu; test "$(cat /robot/data/info/soc_index)" = 0; test -f ' + watcher +
              '; for u in t1-fall-skill t1-fall-case-display t1-fall-display; do '
              'if systemctl is-active --quiet "$u.service"; then exit 1; fi; done; ' + shlex.join(player))
    try:
        command(SSH + [remote], stdin=subprocess.DEVNULL, timeout=18)
    except (OSError, subprocess.SubprocessError) as error:
        os.environ['T1_FALL_NO_PREVIEW'] = '1'
        print('显示节点未能启动，看护将在 Jetson 继续。', type(error).__name__, file=sys.stderr, flush=True)
    os.execl('/bin/bash', 'bash', str(ROOT / 'scripts/session.sh'))


def cleanup():
    # Another case or another skill may own the screen. Stop only our tagged unit.
    tag = session_tag()
    script = (f'owner=$(systemctl show {DISPLAY} -p Description --value); '
              f'if test "$owner" = {shlex.quote(tag)}; then sudo -n systemctl stop {DISPLAY}; fi')
    try:
        restore_voice(tag)
    finally:
        command(SSH + [script], stdin=subprocess.DEVNULL, timeout=35)


def restore_voice(tag):
    if voice_owner() == tag:
        command(['/bin/bash', str(ROOT / 'scripts/session.sh'), 'restore-voice', tag], timeout=26)


def stop():
    if unit_state(WATCH) not in IDLE:
        command(['sudo', '-n', 'systemctl', 'stop', WATCH], timeout=75)
    else:
        pending = voice_owner()
        if pending:
            restore_voice(pending)
    result = status()
    if not result['resources_released']:
        raise RuntimeError('请查看 status，核对语音和显示是否已归还。')
    return result


def start():
    parse(read_private(CONFIG))
    if any(unit_state(unit) not in IDLE for unit in (WATCH, 't1-fall-watch.service')):
        raise RuntimeError('Jetson 已有看护进程，请先查看 status。')
    tag = 't1-fall-' + uuid4().hex
    args = ['sudo', '-n', 'systemd-run', '--wait', '--collect', '--unit=' + WATCH,
            '--uid=run', '--gid=run', '--description=' + tag, '--setenv=T1_FALL_SESSION=' + tag,
            '--property=UMask=0077', '--property=TimeoutStopSec=60',
            f'--property=ExecStopPost=/usr/bin/python3 -B "{ROOT / "main.py"}" cleanup',
            '/usr/bin/python3', '-B', str(ROOT / 'main.py'), 'case']
    print('正在从 Jetson 启动一轮看护。进度：journalctl -fu ' + WATCH + ' -o cat', file=sys.stderr, flush=True)
    try:
        command(args, stdout=sys.stderr)
    except KeyboardInterrupt:
        owner = command(['systemctl', 'show', WATCH, '-p', 'Description', '--value'],
                        capture_output=True, text=True).stdout.strip()
        if owner == tag:
            stop()
        raise
    return '本轮看护已结束。检测、问答和短信结果已写入 Jetson 日志。'


def main():
    parser = argparse.ArgumentParser(description='Jetson 上的 T1 单次倒地看护')
    parser.add_argument('action', choices=('install', 'configure', 'start', 'status', 'stop', 'case', 'cleanup'))
    parser.add_argument('--import-legacy', action='store_true', help='首次 install 时导入 Jetson 本地旧版阿里云设置')
    args = parser.parse_args()
    if args.import_legacy and args.action not in ('install', 'configure'):
        parser.error('--import-legacy 仅适用于 install')
    try:
        check_board()
        if args.action in ('install', 'configure'):
            result = install(args.import_legacy)
        elif args.action == 'start':
            # The operator runs the system CLI; YAML belongs to the installed environment.
            env = BASE / 'venv'
            if Path(sys.prefix) != env:
                python = env / 'bin/python'
                if not python.is_file():
                    raise RuntimeError('请先运行 python3 main.py install。')
                os.execv(str(python), [str(python), '-B', str(ROOT / 'main.py'), 'start'])
            result = start()
        elif args.action in ('case', 'cleanup'):
            (run_case if args.action == 'case' else cleanup)()
            return 0
        else:
            result = {'status': status, 'stop': stop}[args.action]()
        if isinstance(result, str):
            result = dict(status='success', message=result)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        result = dict(status='failed', message=f'{args.action} 未完成：{str(error) or "已取消"}',
                      resources_released=False)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return int(result['status'] != 'success')


if __name__ == '__main__':
    sys.exit(main())
