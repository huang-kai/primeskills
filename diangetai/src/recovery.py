"""点歌台自己的持久操作记录；状态未知时不把机器人资源当作已释放。"""
from contextlib import contextmanager, ExitStack
import fcntl
from pathlib import Path

from .support import AudioError

RECORDS = ('media.json', 'music_effects.json', 'quad_effects_music.json',
           'diangetai/media.json', 'diangetai/music_effects.json', 'diangetai/quad_effects_music.json')


@contextmanager
def exclusive(runtime, names=('command.lock', 'music.lock')):
    with ExitStack() as stack:
        for name in names:
            lock = stack.enter_context((Path(runtime) / name).open('a'))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AudioError('已有点歌任务运行，不能恢复或接管。') from None
        yield


def status(runtime):
    runtime = Path(runtime)
    try:
        with exclusive(runtime):
            running = False
            pending = [name for name in RECORDS if (runtime / name).exists()]
    except AudioError:
        running = True
        pending = [name for name in RECORDS if (runtime / name).exists()]
    except OSError:
        return {'status': 'failed', 'message': '无法核对点歌运行目录。',
                'resources_released': False, 'recovery_required': True}
    released = not pending and not running
    return {'status': 'success' if released else 'failed',
            'message': '点歌台没有未确认操作。' if released else
                       ('点歌任务仍在运行。' if running else '存在未确认操作，请检查或运行 recover。'),
            'resources_released': released, 'recovery_required': bool(pending),
            'running': running, 'pending': pending}


def recover(runtime):
    runtime = Path(runtime)
    with exclusive(runtime):
        if (runtime / 'media.json').exists():
            from .robot.transport import Robot
            from .robot.native_media import NativeMedia
            robot = Robot(runtime, lambda: None)
            media = None
            try:
                media = NativeMedia(robot, runtime / 'media.json')
                media.recover()
            finally:
                try:
                    if media is not None:
                        media.close()
                finally:
                    robot.close()
        effects = [name for name in RECORDS[1:] if (runtime / name).exists()]
        if effects:
            raise AudioError('仍有联动或旧目录恢复记录，须现场核对动作、表情和灯光；未自动执行运动或删除记录。')
    return status(runtime)
