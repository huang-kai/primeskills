"""独立配置、私有登录态与计时；不导入宿主框架。"""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import signal
import tempfile
import threading
import time

import yaml


class SetupError(ValueError):
    pass


class AudioError(SetupError):
    pass


class Cancelled(RuntimeError):
    pass


@contextmanager
def cancellation(immediate=False):
    """普通 CLI 信号转为协作取消，finally 仍可完成原厂资源清理。"""
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
            raise Cancelled('任务已取消。')
    try:
        yield check
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class ConfigLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key, value in self.construct_pairs(node, deep=deep):
            if not isinstance(key, str) or key in result:
                raise ValueError('配置键须为唯一文本')
            result[key] = value
        return result


def skill_data(name='diangetai'):
    fallback = Path(os.environ.get('PRIMEBUDDY_HOME', Path.home() / '.primebuddy')) / 'data' / name
    path = Path(os.environ.get('DIANGETAI_DATA', os.environ.get('PRIMEBUDDY_SKILL_DATA', fallback))).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def runtime_dir(data, explicit=None):
    path = Path(explicit or os.environ.get('DIANGETAI_RUNTIME', data / 'runtime')).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def config_path(data, explicit=None):
    if explicit or os.environ.get('DIANGETAI_CONFIG'):
        return Path(explicit or os.environ['DIANGETAI_CONFIG']).expanduser()
    legacy = Path(os.environ.get('PRIMEBUDDY_HOME', Path.home() / '.primebuddy')) / 'cfg/skills/diangetai/config.yaml'
    return Path(os.environ.get('PRIMEBUDDY_SKILL_CONFIG', data / 'config.yaml' if (data / 'config.yaml').exists() else legacy)).expanduser()


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


def read_json(path):
    if not path.exists():
        return {}
    if path.stat().st_mode & 0o077:
        raise SetupError('登录态权限须为 600。')
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise SetupError('登录态无法读取。') from None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_config(path, *, private=True):
    if not path.exists():
        return {}
    if private and path.stat().st_mode & 0o077:
        raise SetupError('配置文件权限须为 600。')
    try:
        value = yaml.load(path.read_text(), Loader=ConfigLoader)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, yaml.YAMLError):
        raise SetupError('配置文件格式无效。') from None


@contextmanager
def measure(stage, message=None):
    start = time.monotonic()
    try:
        yield
    finally:
        logging.getLogger(__name__).info('STAGE_TIME %s %.3f', stage, time.monotonic() - start)
