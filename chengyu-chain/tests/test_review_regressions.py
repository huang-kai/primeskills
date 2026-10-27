"""独立审阅发现的会话边界；本地虚拟设备，不使用真实模型或机器人。"""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.simulation import Fixture, FixtureModels


class ReviewRegressions(unittest.TestCase):
    def test_robot_hint_work_cannot_run_past_independent_idle_deadline(self):
        class SlowHints(FixtureModels):
            clock = None
            gate = None
            worker = None
            until = 0

            def __init__(self):
                super().__init__()
                self.completed = threading.Event()

            def synthesize(self, text):
                if text.startswith('请接'):
                    self.worker = threading.current_thread()
                    self.gate = threading.Event()
                    self.completed.clear()
                    self.until = self.clock() + 80
                    if not self.gate.wait(2):
                        raise AssertionError('测试没有释放提示 TTS')
                audio = super().synthesize(text)
                self.completed.set()
                return audio

        models = SlowHints()
        with Fixture(models=models) as fixture:
            models.clock = fixture.clock
            original_spin = fixture.robot.spin

            def advance_while_computing():
                original_spin()
                if models.gate is not None and fixture.clock() >= models.until:
                    models.gate.set()
                    self.assertTrue(models.completed.wait(1))

            fixture.robot.spin = advance_while_computing
            try:
                result = fixture.session.run()
            finally:
                if models.gate is not None:
                    models.gate.set()
                if models.worker is not None:
                    models.worker.join(1)
            deadline = fixture.robot.windows[0]['inactivity_deadline']
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(fixture.media.played[-1]['audio'], fixture.fallback_audio)
            self.assertLessEqual(fixture.media.played[-1]['at'], deadline + .05,
                            '机器人自己的提示处理不应把独立两分钟空闲上限延长一分钟')
            self.assertTrue(all(play['at'] < deadline for play in fixture.media.played[:-1]),
                            '空闲期限已到后不能继续播报下一条普通提示')
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)
            self.assertEqual(fixture.session.game.snapshot()['wrong_attempts'], 0)


if __name__ == '__main__':
    unittest.main()
