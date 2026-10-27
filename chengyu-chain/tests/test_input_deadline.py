"""临界回答只获得一次完整识别预算，不累加 ASR 和 LLM 的上限。"""
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.simulation import Fixture, FixtureModels
from chengyu_game.support import AudioError


class InputDeadlineTests(unittest.TestCase):
    def test_result_arriving_after_budget_cannot_commit_score_before_worker_returns(self):
        class LateAnswer(FixtureModels):
            def complete(self, messages):
                if json.loads(messages[-1]['content'])['operation'] == 'respond':
                    self.clock.value += 31
                return super().complete(messages)

        models = LateAnswer()
        with Fixture([{'text': '一心一意'}], models=models) as fixture:
            models.clock = fixture.clock
            with self.assertRaisesRegex(AudioError, '超时'):
                fixture.session.run()
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)
            self.assertEqual(fixture.effects.count, 0)
            self.assertEqual(len(fixture.media.played), 1)

    def test_late_unclear_input_shares_one_30_second_recognition_budget(self):
        class SlowInput(FixtureModels):
            gate = None
            worker = None
            until = 0
            heard_at = None

            def delay(self):
                self.worker = threading.current_thread()
                self.gate = threading.Event()
                self.until = self.clock() + 20
                if not self.gate.wait(2):
                    raise AssertionError('测试没有释放识别线程')

            def transcribe(self, data):
                self.heard_at = self.clock()
                self.delay()
                return super().transcribe(data)

            def complete(self, messages):
                if json.loads(messages[-1]['content'])['operation'] == 'respond':
                    self.delay()
                return super().complete(messages)

        models = SlowInput()
        with Fixture([{'text': '无意义噪声', 'wait': 119}], models=models) as fixture:
            models.clock = fixture.clock
            original_spin = fixture.robot.spin

            def advance():
                original_spin()
                if models.gate is not None and fixture.clock() >= models.until:
                    models.gate.set()

            fixture.robot.spin = advance
            try:
                result = fixture.session.run()
            finally:
                if models.gate is not None:
                    models.gate.set()
                if models.worker is not None:
                    models.worker.join(1)
            self.assertEqual(result['reason'], 'inactivity')
            self.assertLessEqual(fixture.media.played[-1]['at'], models.heard_at + 30.05)
            self.assertEqual(fixture.media.played[-1]['audio'], fixture.fallback_audio)
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)
            self.assertEqual(fixture.session.game.snapshot()['wrong_attempts'], 0)
            self.assertEqual(len({w['inactivity_deadline'] for w in fixture.robot.windows}), 1)


if __name__ == '__main__':
    unittest.main()
