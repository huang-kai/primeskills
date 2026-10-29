"""私有恢复记录、协作取消与单进程锁。"""
from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import stat
import tempfile
import threading
import time


class SetupError(ValueError):
    pass


class AudioError(SetupError):
    pass


class PlaybackInterrupted(AudioError):
    """自有音频已确认停止，但未完整播完；可由交互会话继续等待回答。"""


class Cancelled(RuntimeError):
    pass


_stopped = threading.Event()


def check_cancel():
    if _stopped.is_set():
        raise Cancelled('游戏已取消。')


def progress(text):
    logging.getLogger(__name__).info('%s', text)


@contextmanager
def measure(stage):
    """仅记录固定阶段名和单调时钟，不记录话语、凭据或资源地址。"""
    started, ok = time.monotonic(), False
    try:
        yield
        ok = True
    finally:
        finished = time.monotonic()
        logging.getLogger(__name__).info('GAME_STAGE stage=%s at=%.3f seconds=%.3f ok=%s',
                                       stage, finished, finished - started, ok)


@contextmanager
def cancellation():
    _stopped.clear()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, lambda *_: _stopped.set())
    try:
        yield check_cancel
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def private_dir(path):
    path = Path(path).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_json(path, value):
    path = Path(path)
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path):
    path = Path(path)
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise SetupError('恢复记录须为私有普通文件，权限 600。')
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise SetupError('恢复记录无法读取，未丢弃记录。') from None


@contextmanager
def exclusive(runtime):
    private_dir(runtime)
    fd = os.open(runtime / 'command.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('成语游戏仍在运行，请先结束当前游戏。') from None
        yield
    finally:
        os.close(fd)


def is_running(runtime):
    path = Path(runtime) / 'command.lock'
    if not path.exists():
        return False
    with path.open('r') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


@contextmanager
def frontend_lock():
    """同一控制器上的应用共享原厂前端所有权；hosted 子应用沿用宿主锁。"""
    try:
        fd = os.open('/tmp/primebot-voice-frontend.lock',
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        raise AudioError('原厂语音前端锁不可用或不安全。') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AudioError('原厂语音前端锁须为当前用户的私有普通文件。')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AudioError('原厂语音前端正在由 Buddy 或其他应用使用，请先退出该会话。') from None
        yield
    finally:
        os.close(fd)
