"""独立 CLI 的易来配置；账号仍由 yeelight-home 管理。"""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import tempfile
import threading
import yaml

DEFAULTS = yaml.safe_load((Path(__file__).resolve().parents[1] / 'cfg/config.yaml').read_text())
DEFAULT_EXECUTABLE = DEFAULTS['executable']


class Cancelled(RuntimeError):
    pass


class SkillError(ValueError):
    pass


@contextmanager
def cancellation(immediate=False):
    stopped = threading.Event()
    previous = {}
    def stop(*_):
        if immediate:
            raise KeyboardInterrupt()
        stopped.set()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, stop)
    def check():
        if stopped.is_set():
            raise Cancelled('任务已取消，已发出的灯控状态仍须核实。')
    try:
        yield check
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def paths(config=None, data=None, runtime=None):
    home = Path(os.environ.get('PRIMEBUDDY_HOME', Path.home() / '.primebuddy'))
    data = Path(data or os.environ.get('YEELIGHT_DATA', os.environ.get('PRIMEBUDDY_SKILL_DATA', home / 'data/yeelight'))).expanduser()
    runtime = Path(runtime or os.environ.get('YEELIGHT_RUNTIME', data / 'runtime')).expanduser()
    for path in (data, runtime):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    fallback = data / 'config.yaml' if (data / 'config.yaml').exists() else home / 'cfg/skills/yeelight/config.yaml'
    config = Path(config or os.environ.get('YEELIGHT_CONFIG', os.environ.get('PRIMEBUDDY_SKILL_CONFIG', fallback))).expanduser()
    return config, data, runtime


def read_config(path):
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise SkillError('配置文件权限须为 600。')
    class Loader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = {}
            for key, value in self.construct_pairs(node, deep=deep):
                if not isinstance(key, str) or key in result:
                    raise ValueError('配置键须为唯一文本。')
                result[key] = value
            return result
    try:
        value = yaml.load(path.read_text(), Loader=Loader)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, yaml.YAMLError):
        raise SkillError('易来配置文件格式无效。') from None


def write_config(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            yaml.safe_dump(value, output, allow_unicode=True, sort_keys=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
