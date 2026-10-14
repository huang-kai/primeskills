"""技能独占自己的 ROS 节点与执行器，不重入框架语音线程。"""
from contextlib import contextmanager

from primebuddy.integrations.t1.robot_audio import RobotAudio
from .music_effects import MusicEffects


class EffectsRobot(RobotAudio):
    def __init__(self, context):
        self.checkpoint = context.checkpoint
        super().__init__(context.runtime)

    def spin(self, seconds=.05, cancellable=True):
        if cancellable:
            self.checkpoint()
        super().spin(seconds, cancellable)


@contextmanager
def effects_session(context, enabled):
    if not enabled:
        yield None
        return
    robot = EffectsRobot(context)
    effects = None
    try:
        # 沿用旧版记录名，未完成的恢复不能被新技能覆盖。
        effects = MusicEffects(robot, context.runtime / 'quad_effects_music.json')
        effects.inspect()
        effects.require_ready()
        yield effects
    finally:
        try:
            if effects is not None:
                effects.stop_music()
        finally:
            robot.close()
