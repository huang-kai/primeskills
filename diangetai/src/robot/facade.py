"""独立点歌台设备入口：型号装配、音乐播放与联动，不接管原厂语音模式。"""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import threading

from ..support import AudioError
from ..recovery import RECORDS
from .device import detect_device
from .player import AudioOutput
from .transport import Robot


class RobotFacade:
    def __init__(self, runtime):
        self.device = detect_device()
        if any((Path(runtime) / name).exists() for name in RECORDS):
            raise AudioError('存在未确认的播放或联动恢复记录；未开始新的机器人操作。')
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime.chmod(0o700)
        self.player = AudioOutput(self.runtime, lambda: None)
        self.lock = threading.Lock()

    @contextmanager
    def session(self, checkpoint):
        if not self.lock.acquire(blocking=False):
            raise AudioError('已有点歌任务运行。')
        try:
            with (self.runtime / 'music.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise AudioError('已有点歌任务运行。') from None
                self.player.checkpoint = checkpoint
                if self.player.robot is None:
                    self.player.robot = Robot(self.runtime, checkpoint)
                self.player.robot.checkpoint = checkpoint
                yield self
        finally:
            self.lock.release()

    def effects(self, enabled):
        from ..effects.session import effects_session
        return effects_session(self.player.robot, self.device, self.runtime / 'music_effects.json', enabled)

    def play_music(self, source, **options):
        return self.player.play(source, **options)

    def close(self):
        self.player.close()
