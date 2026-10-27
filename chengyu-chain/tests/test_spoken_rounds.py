"""关键阶段的固定播音词和通关边界，不调用模型服务。"""
from copy import deepcopy
import json
import unittest

from test_game import Game, Model, START, FOLLOWING, accepted
from chengyu_game.simulation import Fixture, FixtureModels


class SpokenRoundTests(unittest.TestCase):
    def test_default_progress_and_explicit_victory_for_every_level(self):
        responses = [{'intent': 'start', 'idiom': START}]
        for target in (5, 7, 10):
            responses += [accepted(START, START)] * target
            responses.append({'intent': 'start', 'idiom': START})
        game = Game(Model(*responses))
        game.start()
        for level, target, next_level in (('初级', 5, '中级'), ('中级', 7, '高级'), ('高级', 10, '初级')):
            for count in range(1, target + 1):
                reply = game.respond('一心一意')
                if count < target:
                    self.assertIn(str(count), reply.text)
                    self.assertIn(str(target), reply.text)
                    self.assertEqual(reply.outcome, 'success')
                else:
                    self.assertIn('太棒了', reply.text)
                    self.assertIn('赢', reply.text)
                    self.assertIn(level, reply.text)
                    self.assertIn(next_level, reply.text)
                    self.assertIn('继续', reply.text)
                    self.assertEqual(reply.outcome, 'win')
                    self.assertEqual(game.snapshot()['phase'], 'await_next')
                    self.assertFalse(reply.ended or reply.new_question)
            game.respond('继续')

    def test_final_valid_answer_does_not_depend_on_unused_robot_next_idiom(self):
        for following in (None, {'text': '坏结果'}, FOLLOWING, START, 'absent'):
            with self.subTest(following=following):
                final = accepted(START, following)
                if following == 'absent':
                    del final['next_idiom']
                model = Model({'intent': 'start', 'idiom': START}, *([accepted(START, START)] * 4), final)
                game = Game(model)
                game.start()
                for _ in range(4):
                    game.respond('一心一意')
                reply = game.respond('一心一意')
                self.assertEqual(reply.outcome, 'win')
                self.assertEqual(game.snapshot()['level_successes'], 5)
                self.assertIn('赢', reply.text)

    def test_final_answer_still_validates_user_sound_and_response_fields(self):
        bad = [dict(accepted(START, None), extra='ignored?'),
               accepted(dict(START, pinyin=['shan1', 'xin1', 'yi1', 'yi4']), None)]
        for result in bad:
            with self.subTest(result=result):
                model = Model({'intent': 'start', 'idiom': START}, *([accepted(START, START)] * 4), result)
                game = Game(model)
                game.start()
                for _ in range(4):
                    game.respond('一心一意')
                self.assertFalse(game.respond('一心一意').celebrate)
                self.assertEqual(game.snapshot()['level_successes'], 4)

    def test_partial_configuration_changes_speech_but_not_rules(self):
        speech = {'correct': '很好，本局{successes}/{target}。下一题“{next_idiom}”。',
                  'win': '恭喜！{level}的{target}次挑战成功啦！',
                  'win_next': '准备挑战{next_level}，说继续吧。'}
        model = Model({'intent': 'start', 'idiom': START}, *([accepted(START, START)] * 5))
        game = Game(model, speech=speech)
        self.assertIn('声调', game.start().text)
        for count in range(1, 5):
            self.assertIn(f'本局{count}/5', game.respond('一心一意').text)
        self.assertEqual(game.respond('一心一意').text, '恭喜！初级的5次挑战成功啦！准备挑战中级，说继续吧。')
        self.assertTrue(game.respond('不玩了').ended)

    def test_session_sends_win_text_to_tts_before_next_round_listening(self):
        class Models(FixtureModels):
            def complete(self, messages):
                request = json.loads(messages[-1]['content'])
                if request.get('utterance') == '一心一意':
                    self.requests.append(request)
                    return accepted(deepcopy(START), deepcopy(START))
                return super().complete(messages)

        steps = [{'text': '一心一意'}] * 5 + [{'text': '继续'}, {'text': '不玩了'}]
        with Fixture(steps, models=Models()) as fixture:
            fixture.session.run()
            victories = [text for text in fixture.models.spoken if '赢' in text]
            self.assertEqual(len(victories), 1)
            self.assertIn('初级', victories[0])
            self.assertIn('中级', victories[0])
            self.assertEqual(fixture.effects.outcomes.count('win'), 1)
            self.assertEqual(fixture.session.game.snapshot()['level'], '中级')
            waiting = [window for window in fixture.robot.windows if window['state']['phase'] == 'await_next']
            self.assertEqual(len(waiting), 1)
            self.assertEqual(waiting[0]['state']['level_successes'], 5)


if __name__ == '__main__':
    unittest.main()
