"""设备适配后的联动生命周期；共用 skill 自己的常驻 ROS 节点。"""
from contextlib import contextmanager
from .music_effects import MusicEffects, Q1MusicEffects


@contextmanager
def effects_session(robot, device, record, enabled, music_playing=lambda: False):
    if not enabled:
        yield None
        return
    kind = {'T1': MusicEffects, 'Q1': Q1MusicEffects}[device.series]
    options = {'music_playing': music_playing} if device.series == 'Q1' else {}
    effects = kind(robot, record, **options)
    try:
        effects.inspect()
        effects.require_ready()
        yield effects
    finally:
        try:
            effects.stop_music()
        finally:
            effects.close()
