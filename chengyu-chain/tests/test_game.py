"""纯文本接龙与迟到结果；不调用云模型或机器人。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game import game as MODULE
from chengyu_game.support import AudioError
Game = MODULE.Game


def word(text, *sounds):
    return {'text': text, 'pinyin': list(sounds), 'meaning': '测试用释义。'}


START = word('一心一意', 'yi1', 'xin1', 'yi1', 'yi4')
ANSWER = word('一马当先', 'yi1', 'ma3', 'dang1', 'xian1')
FOLLOWING = word('先声夺人', 'xian1', 'sheng1', 'duo2', 'ren2')


def accepted(answer=ANSWER, following=FOLLOWING):
    return {'intent': 'answer', 'verdict': 'valid', 'idiom': answer, 'next_idiom': following}


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(deepcopy(messages))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def started(*responses, first=START):
    model = Model({'intent': 'start', 'idiom': first}, *responses)
    game = Game(model)
    game.start()
    return game, model


class GameTests(unittest.TestCase):
    def test_constructor_does_not_call_model_and_start_prompts_complete_idiom(self):
        model = Model({'intent': 'start', 'idiom': START})
        game = Game(model)
        self.assertEqual(model.calls, [])
        reply = game.start()
        self.assertIn('一心一意', reply.text)
        self.assertIn('意', reply.text)
        self.assertIn('声调', reply.text)
        self.assertFalse(reply.celebrate or reply.ended)
        self.assertEqual(game.snapshot()['level'], '初级')
        self.assertEqual(game.snapshot()['round_number'], 1)
        self.assertEqual(game.snapshot()['target_successes'], 5)
        self.assertTrue(reply.new_question)

    def test_same_sound_different_character_and_tone_is_accepted(self):
        game, _ = started(accepted())
        reply = game.respond('我接一马当先！', turn_id='first')
        self.assertTrue(reply.celebrate)
        self.assertEqual(game.snapshot()['last_idiom'], '先声夺人')
        self.assertEqual(game.snapshot()['total_successes'], 1)

    def test_diacritic_pinyin_matches_numbered_pinyin(self):
        answer = word('一马当先', 'yī', 'mǎ', 'dāng', 'xiān')
        game, _ = started(accepted(answer))
        self.assertTrue(game.respond('一马当先').celebrate)

    def test_u_and_umlaut_are_not_conflated(self):
        self.assertEqual(MODULE._sound('lǜ'), 'lv')
        self.assertEqual(MODULE._sound('lü4'), 'lv')
        self.assertEqual(MODULE._sound('lu:4'), 'lv')
        self.assertNotEqual(MODULE._sound('lǜ'), MODULE._sound('lù'))

    def test_wrong_sound_keeps_question_and_increases_only_wrong_attempts(self):
        wrong = word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')
        game, _ = started({'intent': 'answer', 'verdict': 'wrong_sound', 'idiom': wrong})
        before = game.snapshot()
        reply = game.respond('山清水秀')
        self.assertFalse(reply.celebrate or reply.ended)
        self.assertEqual(reply.outcome, 'failure')
        self.assertIn('是成语', reply.text)
        self.assertIn('意', reply.text)
        self.assertEqual(game.snapshot(), dict(before, wrong_attempts=1))
        self.assertFalse(reply.new_question)

    def test_non_idiom_and_uncertain_input_do_not_advance(self):
        game, _ = started({'intent': 'answer', 'verdict': 'not_idiom'},
                          {'intent': 'answer', 'verdict': 'uncertain'})
        before = game.snapshot()
        for text in ('我要吃苹果', '一马当仙'):
            reply = game.respond(text)
            self.assertFalse(reply.celebrate)
            self.assertEqual(reply.outcome, '')
            self.assertEqual(game.snapshot(), before)
        self.assertFalse(reply.responded)

    def test_polyphonic_character_uses_contextual_reading(self):
        first = word('意味深长', 'yi4', 'wei4', 'shen1', 'chang2')
        wrong = word('长幼有序', 'zhang3', 'you4', 'you3', 'xu4')
        good = word('长驱直入', 'chang2', 'qu1', 'zhi2', 'ru4')
        following = word('入木三分', 'ru4', 'mu4', 'san1', 'fen1')
        game, _ = started({'intent': 'answer', 'verdict': 'wrong_sound', 'idiom': wrong},
                          accepted(good, following), first=first)
        self.assertFalse(game.respond('长幼有序').celebrate)
        self.assertTrue(game.respond('长驱直入').celebrate)

    def test_models_cannot_claim_valid_when_either_chain_is_broken(self):
        bad_answer = word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')
        bad_following = word('人山人海', 'ren2', 'shan1', 'ren2', 'hai3')
        for response, text in ((accepted(bad_answer), '山清水秀'),
                               (accepted(following=bad_following), '一马当先')):
            with self.subTest(response=response):
                game, _ = started(response)
                before = game.snapshot()
                self.assertFalse(game.respond(text).celebrate)
                self.assertEqual(game.snapshot(), before)

    def test_verified_homophone_transcription_is_repeated_explicitly(self):
        game, _ = started(accepted())
        reply = game.respond('一马当仙')
        self.assertTrue(reply.celebrate)
        self.assertIn('一马当先', reply.text)
        self.assertEqual(game.snapshot()['total_successes'], 1)

    def test_models_cannot_invent_nonhomophone_answer(self):
        game, _ = started(accepted())
        before = game.snapshot()
        reply = game.respond('山清水秀')
        self.assertFalse(reply.celebrate or reply.responded)
        self.assertEqual(game.snapshot(), before)

    def test_front_back_nasals_match_but_other_initials_do_not(self):
        first = word('心心相印', 'xin1', 'xin1', 'xiang1', 'yin4')
        answer = word('迎刃而解', 'ying2', 'ren4', 'er2', 'jie3')
        following = word('解甲归田', 'jie3', 'jia3', 'gui1', 'tian2')
        game, _ = started(accepted(answer, following), first=first)
        self.assertTrue(game.respond('迎刃而解').celebrate)

    def test_inconsistent_wrong_sound_verdict_is_rejected(self):
        game, _ = started({'intent': 'answer', 'verdict': 'wrong_sound', 'idiom': ANSWER})
        reply = game.respond('一马当先')
        self.assertIn('没判断清楚', reply.text)
        self.assertEqual(game.snapshot()['total_successes'], 0)
        self.assertEqual(game.snapshot()['wrong_attempts'], 0)

    def test_explanation_help_and_clarification_keep_current_question(self):
        game, _ = started({'intent': 'explain', 'text': '一心一意就是说心思专一。'},
                          {'intent': 'help', 'text': '想想一匹马跑在前面的成语。'},
                          {'intent': 'clarify', 'text': '要听歌的话，请先结束成语接龙。'})
        before = game.snapshot()
        for text in ('什么意思', '我不会', '我要听歌'):
            reply = game.respond(text)
            self.assertIn('一心一意', reply.text)
            self.assertIn('意', reply.text)
            self.assertFalse(reply.celebrate)
            self.assertEqual(game.snapshot(), before)

    def test_each_level_wins_at_its_target_then_waits_for_continue(self):
        cycle = accepted(START, START)
        game, model = started(*([cycle] * 5), {'intent': 'start', 'idiom': START},
                              *([cycle] * 7), {'intent': 'start', 'idiom': START},
                              *([cycle] * 10), {'intent': 'start', 'idiom': START})
        for round_number, (level, target, next_level) in enumerate(
                [('初级', 5, '中级'), ('中级', 7, '高级'), ('高级', 10, '初级')], 1):
            self.assertEqual(game.snapshot()['level'], level)
            self.assertEqual(game.snapshot()['round_number'], round_number)
            self.assertEqual(game.snapshot()['target_successes'], target)
            for _ in range(target - 1):
                reply = game.respond('一心一意')
                self.assertEqual(reply.outcome, 'success')
                self.assertTrue(reply.new_question)
                self.assertEqual(game.snapshot()['phase'], 'playing')
            reply = game.respond('一心一意')
            self.assertEqual(reply.outcome, 'win')
            self.assertTrue(reply.celebrate)
            self.assertFalse(reply.ended or reply.new_question)
            self.assertIn(next_level, reply.text)
            self.assertEqual(game.snapshot()['phase'], 'await_next')
            self.assertEqual(game.snapshot()['level_successes'], target)
            self.assertEqual(game.snapshot()['next_level'], next_level)
            self.assertEqual(game.snapshot()['level'], level)
            reply = game.respond('继续玩')
            self.assertTrue(reply.new_question)
            self.assertEqual(game.snapshot()['phase'], 'playing')
            self.assertEqual(game.snapshot()['level_successes'], 0)
        self.assertEqual(game.snapshot()['total_successes'], 22)
        self.assertEqual(game.snapshot()['level'], '初级')
        self.assertEqual(json.loads(model.calls[-1][-1]['content'])['operation'], 'start')

    def test_three_verified_wrong_answers_lose_and_retry_same_level(self):
        wrong = {'intent': 'answer', 'verdict': 'wrong_sound',
                 'idiom': word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')}
        game, _ = started(wrong, {'intent': 'answer', 'verdict': 'not_idiom'},
                          accepted(START, START), wrong, wrong, {'intent': 'start', 'idiom': START})
        game.respond('山清水秀')
        game.respond('苹果')
        game.respond('一心一意')
        self.assertEqual(game.snapshot()['wrong_attempts'], 1)
        self.assertEqual(game.respond('山清水秀').outcome, 'failure')
        reply = game.respond('山清水秀')
        self.assertEqual(reply.outcome, 'lose')
        self.assertFalse(reply.ended or reply.new_question)
        self.assertEqual(game.snapshot()['phase'], 'await_next')
        self.assertEqual(game.snapshot()['next_level'], '初级')
        self.assertTrue(game.respond('再来一局').new_question)
        self.assertEqual(game.snapshot()['round_number'], 2)
        self.assertEqual(game.snapshot()['wrong_attempts'], 0)
        self.assertEqual(game.snapshot()['level_successes'], 0)

    def test_manual_level_changes_reset_level_count_not_total_or_question(self):
        game, model = started(accepted(START, START), {'intent': 'adjust', 'change': 'up'},
                              {'intent': 'adjust', 'change': '高级'}, {'intent': 'adjust', 'change': 'down'},
                              {'intent': 'adjust', 'change': '初级'})
        game.respond('一心一意')
        for text, expected in (('难一点', '中级'), ('切到高级', '高级'), ('简单一点', '中级'), ('初级吧', '初级')):
            reply = game.respond(text)
            self.assertFalse(reply.celebrate)
            self.assertEqual(game.snapshot()['level'], expected)
            self.assertEqual(game.snapshot()['level_successes'], 0)
            self.assertEqual(game.snapshot()['total_successes'], 1)
            self.assertEqual(game.snapshot()['last_idiom'], START['text'])
            self.assertTrue(reply.new_question)
            self.assertIn('重新', reply.text)
            self.assertEqual(game.snapshot()['wrong_attempts'], 0)
            self.assertFalse(game.snapshot()['aid_used'])

    def test_level_limits_do_not_wrap(self):
        game, _ = started({'intent': 'adjust', 'change': 'down'}, {'intent': 'adjust', 'change': '高级'},
                          {'intent': 'adjust', 'change': 'up'})
        self.assertIn('已经是初级', game.respond('简单一点').text)
        game.respond('高级')
        self.assertIn('已经是高级', game.respond('再难点').text)
        self.assertEqual(game.snapshot()['level'], '高级')

    def test_explicit_exit_does_not_require_network_and_negation_is_not_exit(self):
        game, model = started()
        self.assertFalse(game.respond('不要结束').ended)
        calls = len(model.calls)
        self.assertTrue(game.respond('我不想玩了！').ended)
        self.assertEqual(len(model.calls), calls)
        self.assertEqual(game.snapshot()['phase'], 'ended')

    def test_wake_and_noise_return_empty_reply_without_changing_state(self):
        game, _ = started({'intent': 'wake'}, {'intent': 'noise'})
        before = game.snapshot()
        for text in ('机器人唤醒词', '嘶嘶嘶'):
            reply = game.respond(text)
            self.assertEqual(reply.text, '')
            self.assertFalse(reply.ended or reply.celebrate)
            self.assertFalse(reply.responded or reply.new_question)
            self.assertEqual(game.snapshot(), before)

    def test_model_understands_other_exit_intents(self):
        game, _ = started({'intent': 'exit'})
        self.assertTrue(game.respond('先休息一下今天就到这吧').ended)
        self.assertEqual(game.snapshot()['total_successes'], 0)

    def test_duplicate_turn_does_not_count_or_celebrate_again(self):
        game, model = started(accepted(START, START))
        self.assertTrue(game.respond('一心一意', turn_id='turn-1').celebrate)
        before = game.snapshot()
        self.assertEqual(game.respond('一心一意', turn_id='turn-1').text, '')
        self.assertEqual(game.snapshot(), before)
        self.assertEqual(len(model.calls), 2)

    def test_close_discards_inflight_answer(self):
        entered, release = threading.Event(), threading.Event()
        def complete(messages):
            if json.loads(messages[-1]['content'])['operation'] == 'start':
                return {'intent': 'start', 'idiom': START}
            entered.set()
            self.assertTrue(release.wait(2))
            return accepted()
        game = Game(complete)
        game.start()
        replies = []
        thread = threading.Thread(target=lambda: replies.append(game.respond('一马当先')))
        thread.start()
        self.assertTrue(entered.wait(2))
        game.close()
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[0].text, '')
        self.assertTrue(replies[0].ended)
        self.assertFalse(replies[0].celebrate)
        self.assertEqual(game.snapshot()['total_successes'], 0)
        self.assertEqual(game.snapshot()['last_idiom'], START['text'])

    def test_bad_model_outputs_preserve_state_and_do_not_leak_errors(self):
        bad_word = word('一马当先', 'yi1')
        responses = [None, [], {'intent': 'dance'}, {'intent': 'adjust', 'change': []},
                     {'intent': 'answer', 'verdict': True}, accepted(bad_word),
                     dict(accepted(), execute='motor'), {'intent': 'explain', 'text': '\x00'},
                     {'intent': 'explain', 'text': '长' * 241}, RuntimeError('secret-test-value')]
        for result in responses:
            with self.subTest(result=result):
                game, model = started(result)
                before = game.snapshot()
                reply = game.respond('一马当先')
                self.assertFalse(reply.celebrate or reply.ended)
                self.assertFalse(reply.responded)
                self.assertEqual(game.snapshot(), before)
                self.assertNotIn('secret-test-value', reply.text)
                self.assertEqual(len(model.calls), 2)

    def test_failed_start_ends_and_cannot_restart_or_invoke_model_again(self):
        game = Game(Model({'intent': 'start', 'idiom': {'text': '假的'}}))
        self.assertTrue(game.start().ended)
        self.assertTrue(game.start().ended)
        self.assertTrue(game.respond('一心一意').ended)

    def test_start_is_idempotent_and_snapshots_do_not_mutate_game(self):
        game, model = started()
        before = game.snapshot()
        self.assertIn('一心一意', game.start().text)
        copy = game.state
        copy['level'] = '高级'
        self.assertEqual(game.snapshot(), before)
        self.assertEqual(len(model.calls), 1)

    def test_timed_hints_are_local_and_do_not_change_any_state(self):
        game, model = started()
        before = game.snapshot()
        for _ in range(2):
            reply = game.hint()
            self.assertIn('意', reply.text)
            self.assertFalse(reply.new_question or reply.responded)
        self.assertEqual(game.snapshot(), before)
        self.assertEqual(len(model.calls), 1)

    def test_first_timeout_demonstrates_chain_without_counting_success_second_loses(self):
        game, model = started({'intent': 'assist', 'idiom': ANSWER, 'next_idiom': FOLLOWING})
        reply = game.timeout()
        self.assertIn('一马当先', reply.text)
        self.assertIn('先声夺人', reply.text)
        self.assertTrue(reply.new_question)
        self.assertFalse(reply.responded or reply.celebrate)
        self.assertTrue(game.snapshot()['aid_used'])
        self.assertEqual(game.snapshot()['last_idiom'], FOLLOWING['text'])
        self.assertEqual(game.snapshot()['question_number'], 2)
        self.assertEqual(game.snapshot()['total_successes'], 0)
        self.assertEqual(game.snapshot()['wrong_attempts'], 0)
        self.assertEqual(json.loads(model.calls[-1][-1]['content'])['operation'], 'assist')
        reply = game.timeout()
        self.assertEqual(reply.outcome, 'lose')
        self.assertFalse(reply.ended or reply.responded)
        self.assertEqual(game.snapshot()['phase'], 'await_next')
        self.assertEqual(len(model.calls), 2)

    def test_bad_assist_cannot_loop_forever_or_invent_an_answer(self):
        bad_next = word('人山人海', 'ren2', 'shan1', 'ren2', 'hai3')
        for result in (RuntimeError('private-test-value'), {'intent': 'assist', 'idiom': ANSWER,
                                                         'next_idiom': bad_next}):
            with self.subTest(result=result):
                game, model = started(result)
                before = game.snapshot()
                reply = game.timeout()
                self.assertFalse(reply.responded or reply.celebrate or reply.ended)
                self.assertTrue(reply.new_question)
                self.assertNotIn('private-test-value', reply.text)
                self.assertNotIn('一马当先', reply.text)
                self.assertEqual(game.snapshot(), dict(before, aid_used=True))
                self.assertEqual(game.timeout().outcome, 'lose')
                self.assertEqual(len(model.calls), 2)

    def test_manual_level_change_resets_errors_aid_and_starts_new_round(self):
        wrong = {'intent': 'answer', 'verdict': 'wrong_sound',
                 'idiom': word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')}
        game, _ = started(wrong, {'intent': 'assist', 'idiom': ANSWER, 'next_idiom': FOLLOWING},
                          {'intent': 'adjust', 'change': '中级'})
        game.respond('山清水秀')
        game.timeout()
        before = game.snapshot()
        reply = game.respond('中级')
        self.assertTrue(reply.new_question)
        self.assertEqual(game.snapshot()['round_number'], 2)
        self.assertEqual(game.snapshot()['question_number'], 1)
        self.assertEqual(game.snapshot()['last_idiom'], before['last_idiom'])
        self.assertEqual(game.snapshot()['wrong_attempts'], 0)
        self.assertFalse(game.snapshot()['aid_used'])

    def test_duplicate_wrong_answer_is_not_counted_twice(self):
        game, model = started({'intent': 'answer', 'verdict': 'wrong_sound',
                               'idiom': word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')})
        game.respond('山清水秀', turn_id='same')
        reply = game.respond('山清水秀', turn_id='same')
        self.assertFalse(reply.responded)
        self.assertEqual(game.snapshot()['wrong_attempts'], 1)
        self.assertEqual(len(model.calls), 2)

    def test_late_assist_after_exit_cannot_speak_or_advance(self):
        entered, release = threading.Event(), threading.Event()

        def complete(messages):
            if json.loads(messages[-1]['content'])['operation'] == 'start':
                return {'intent': 'start', 'idiom': START}
            entered.set()
            self.assertTrue(release.wait(2))
            return {'intent': 'assist', 'idiom': ANSWER, 'next_idiom': FOLLOWING}

        game = Game(complete)
        game.start()
        replies = []
        thread = threading.Thread(target=lambda: replies.append(game.timeout()))
        thread.start()
        self.assertTrue(entered.wait(2))
        self.assertTrue(game.respond('结束游戏').ended)
        release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(replies[0].text, '')
        self.assertFalse(replies[0].new_question or replies[0].responded)
        self.assertEqual(game.snapshot()['last_idiom'], START['text'])

    def test_await_next_exit_is_local_and_failed_restart_keeps_session_open(self):
        game, _ = started(RuntimeError('unavailable'), RuntimeError('restart unavailable'))
        game.timeout()
        game.timeout()
        reply = game.respond('再来一局')
        self.assertFalse(reply.ended or reply.responded)
        self.assertEqual(game.snapshot()['phase'], 'await_next')
        self.assertTrue(game.respond('结束游戏').ended)

    def test_await_next_does_not_score_answers_or_repeat_timeout_effects(self):
        cycle = accepted(START, START)
        game, model = started(*([cycle] * 6))
        for _ in range(5):
            game.respond('一心一意')
        before = game.snapshot()
        reply = game.respond('一心一意')
        self.assertFalse(reply.celebrate or reply.responded or reply.new_question)
        self.assertEqual(game.timeout().text, '')
        self.assertEqual(game.hint().text, '')
        self.assertEqual(game.snapshot(), before)
        self.assertEqual(len(model.calls), 7)

    def test_semantic_continue_starts_only_after_round_has_ended(self):
        game, model = started(*([accepted(START, START)] * 5),
                              {'intent': 'continue', 'idiom': FOLLOWING})
        for _ in range(5):
            game.respond('一心一意')
        reply = game.respond('我想试试下一关')
        self.assertTrue(reply.new_question)
        self.assertEqual(game.snapshot()['round_number'], 2)
        self.assertEqual(game.snapshot()['level'], '中级')
        self.assertEqual(game.snapshot()['last_idiom'], FOLLOWING['text'])
        self.assertEqual(json.loads(model.calls[-1][-1]['content'])['state']['phase'], 'await_next')

    def test_bad_assist_first_link_cannot_consume_wrong_attempt_or_replace_question(self):
        game, _ = started({'intent': 'assist', 'idiom': FOLLOWING, 'next_idiom': FOLLOWING})
        before = game.snapshot()
        reply = game.timeout()
        self.assertEqual(reply.outcome, '')
        self.assertTrue(reply.new_question)
        self.assertEqual(game.snapshot(), dict(before, aid_used=True))

    def test_invalid_next_word_before_winning_answer_cannot_increment_scores(self):
        game, _ = started(*([accepted(START, START)] * 3), accepted(START, FOLLOWING))
        for _ in range(3):
            game.respond('一心一意')
        before = game.snapshot()
        reply = game.respond('一心一意')
        self.assertFalse(reply.responded or reply.new_question)
        self.assertEqual(reply.outcome, '')
        self.assertEqual(game.snapshot(), before)

    def test_duplicate_turn_is_still_discarded_after_new_round(self):
        game, model = started(*([accepted(START, START)] * 5), {'intent': 'start', 'idiom': START})
        for number in range(5):
            game.respond('一心一意', turn_id=number)
        game.respond('再来一局', turn_id='continue')
        before = game.snapshot()
        self.assertEqual(game.respond('一心一意', turn_id=0).text, '')
        self.assertEqual(game.snapshot(), before)
        self.assertEqual(len(model.calls), 7)

    def test_late_model_result_is_rejected_before_count_or_question_commit(self):
        now = [0]
        model = Model({'intent': 'start', 'idiom': START}, accepted())

        def complete(messages):
            result = model(messages)
            if json.loads(messages[-1]['content'])['operation'] == 'respond':
                now[0] = 31
            return result

        game = Game(complete)
        game.start()
        before = game.snapshot()

        def check_deadline():
            self.assertEqual(game.snapshot(), before)
            if now[0] >= 30:
                raise AudioError('response deadline expired')

        with self.assertRaisesRegex(AudioError, 'deadline expired'):
            game.respond('一马当先', before_commit=check_deadline)
        self.assertEqual(game.snapshot(), before)
        # 过期 worker 必须释放 inflight，让会话能继续处理本题超时。
        model.responses.append({'intent': 'assist', 'idiom': ANSWER, 'next_idiom': FOLLOWING})
        self.assertTrue(game.timeout().new_question)

    def test_valid_answer_commits_after_guard_and_default_interface_still_works(self):
        game, _ = started(accepted(START, START), accepted())
        before, calls = game.snapshot(), []

        def check_deadline():
            self.assertEqual(game.snapshot(), before)
            calls.append(True)

        self.assertEqual(game.respond('一心一意', before_commit=check_deadline).outcome, 'success')
        self.assertEqual(calls, [True])
        self.assertEqual(game.respond('一马当先').outcome, 'success')

    def test_expired_guard_does_not_commit_error_or_manual_level(self):
        wrong = {'intent': 'answer', 'verdict': 'wrong_sound',
                 'idiom': word('山清水秀', 'shan1', 'qing1', 'shui3', 'xiu4')}

        class DeadlineExpired(Exception):
            pass

        def expired():
            raise DeadlineExpired('expired')

        for result, text in ((wrong, '山清水秀'), ({'intent': 'adjust', 'change': '中级'}, '中级')):
            with self.subTest(intent=result['intent']):
                game, _ = started(result)
                before = game.snapshot()
                with self.assertRaises(DeadlineExpired):
                    game.respond(text, before_commit=expired)
                self.assertEqual(game.snapshot(), before)

    def test_expired_guard_cannot_start_initial_game_or_either_restart_path(self):
        def expired():
            raise AudioError('late start')

        game = Game(Model({'intent': 'start', 'idiom': START}))
        before = game.snapshot()
        with self.assertRaises(AudioError):
            game.start(before_commit=expired)
        self.assertEqual(game.snapshot(), before)
        for text, result in (('再来一局', {'intent': 'start', 'idiom': START}),
                             ('我想试试下一关', {'intent': 'continue', 'idiom': START})):
            with self.subTest(text=text):
                game, _ = started(*([accepted(START, START)] * 5), result)
                for _ in range(5):
                    game.respond('一心一意')
                before = game.snapshot()
                with self.assertRaises(AudioError):
                    game.respond(text, before_commit=expired)
                self.assertEqual(game.snapshot(), before)

    def test_exit_remains_immediate_when_commit_guard_would_expire(self):
        game, _ = started()

        def expired():
            self.fail('退出不需要通过计分提交守卫')

        self.assertTrue(game.respond('结束游戏', before_commit=expired).ended)


if __name__ == '__main__':
    unittest.main()
