"""虚拟时间验收：题目期限、单次援助、局间等待与应用退出。"""
import json
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.simulation import Fixture, FixtureModels, FIRST, FIRST_ANSWER, SECOND, WRONG


class RoundModels(FixtureModels):
    def complete(self, messages):
        request = json.loads(messages[-1]['content'])
        if request['operation'] == 'assist':
            self.requests.append(request)
            return {'intent': 'assist', 'idiom': deepcopy(FIRST_ANSWER), 'next_idiom': deepcopy(SECOND)}
        return super().complete(messages)


class RoundSessionTests(unittest.TestCase):
    def test_all_three_levels_win_and_only_explicit_exit_releases_application(self):
        class CycleModels(RoundModels):
            def complete(self, messages):
                request = json.loads(messages[-1]['content'])
                if request.get('utterance') == '一心一意':
                    self.requests.append(request)
                    return {'intent': 'answer', 'verdict': 'valid', 'idiom': deepcopy(FIRST),
                            'next_idiom': deepcopy(FIRST)}
                return super().complete(messages)
        texts = ['一心一意'] * 5 + ['继续'] + ['一心一意'] * 7 + ['继续'] + ['一心一意'] * 10
        texts += ['再来一局', '不玩了']
        with Fixture([{'text': text} for text in texts], models=CycleModels()) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'user_exit')
            self.assertEqual(fixture.effects.outcomes.count('win'), 3)
            state = fixture.session.game.snapshot()
            self.assertEqual((state['total_successes'], state['level'], state['round_number']), (22, '初级', 4))
            self.assertEqual(state['level_successes'], 0)

    def test_silence_gets_one_aid_then_round_loss_and_application_timeout(self):
        with Fixture(models=RoundModels()) as fixture:
            result = fixture.session.run()
            requests = fixture.models.requests
            self.assertEqual(sum(r['operation'] == 'assist' for r in requests), 1)
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)
            self.assertIn('lose', fixture.effects.outcomes)
            self.assertEqual(fixture.effects.outcomes.count('lose'), 1)
            self.assertTrue(fixture.models.spoken[1].startswith('请接'))
            self.assertTrue(fixture.models.spoken[2].startswith('请接'))
            self.assertLess(fixture.media.played[2]['at'], fixture.robot.windows[0]['at'] + 21)
            # 自动提醒和援助不能把应用无回应期限一直延长。
            self.assertLess(fixture.clock(), 1130)

    def test_three_real_wrong_idioms_end_only_the_round_then_user_can_restart(self):
        steps = [{'text': '山清水秀'}] * 3 + [{'text': '再来一局'}, {'text': '一马当先'}, {'text': '不玩了'}]
        with Fixture(steps, models=RoundModels()) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'user_exit')
            self.assertEqual(fixture.effects.outcomes.count('lose'), 1)
            self.assertEqual(fixture.effects.outcomes, ['failure', 'failure', 'lose', 'success'])
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 1)
            self.assertEqual(fixture.session.game.snapshot()['round_number'], 2)

    def test_unrecognized_speech_never_counts_as_wrong_idiom(self):
        with Fixture([{'text': ''}, {'text': ''}, {'text': ''}, {'text': '不玩了'}], models=RoundModels()) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'user_exit')
            self.assertEqual(fixture.session.game.snapshot()['wrong_attempts'], 0)
            self.assertNotIn('lose', fixture.effects.outcomes)
            self.assertNotIn('failure', fixture.effects.outcomes)

    def test_noise_and_wake_do_not_keep_application_alive(self):
        steps = [{'text': '无意义噪声', 'wait': 8}, {'text': '元仔元仔', 'wait': 8}] * 8
        with Fixture(steps, models=RoundModels()) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'inactivity')
            self.assertLess(fixture.clock(), 1135)


if __name__ == '__main__':
    unittest.main()
