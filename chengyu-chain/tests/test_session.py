"""真实 Session/Game/VoiceInput 组合；只使用虚拟设备、时间和模型。"""
import io
import json
from pathlib import Path
import sys
import threading
import unittest
import wave


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.providers import ProviderNetworkError
from chengyu_game.session import Session, TIMEOUT_TEXT, synthesize
from chengyu_game.simulation import Fixture, FixtureModels, simulate, synthetic_wav
from chengyu_game.support import AudioError, Cancelled


class SessionTests(unittest.TestCase):
    def test_configured_correct_speech_reaches_game_and_playback(self):
        with Fixture([{'text': '一马当先'}, {'text': '不玩了'}]) as fixture:
            fixture.session = Session(fixture.models, fixture.robot, fixture.voice, fixture.media, fixture.effects,
                                      fixture.fallback, clock=fixture.clock,
                                      speech={'correct': '接得好！我接“{next_idiom}”。'})
            fixture.robot.session = fixture.session
            self.assertEqual(fixture.session.run()['reason'], 'user_exit')
            self.assertTrue(any('接得好！我接“先声夺人”。' in text for text in fixture.models.spoken))

    def test_turn_diagnostics_include_state_without_user_or_spoken_text(self):
        with Fixture([{'text': '一马当先'}, {'text': '不玩了'}]) as fixture:
            with self.assertLogs('chengyu_game.session', level='INFO') as captured:
                fixture.session.run()
            turns = [line for line in captured.output if 'GAME_TURN' in line]
            self.assertEqual(len(turns), 2)
            for line in turns:
                for field in ('phase=', 'level_successes=', 'target=', 'outcome='):
                    self.assertIn(field, line)
                self.assertNotIn('一马当先', line)
                self.assertNotIn('不玩了', line)

    def test_full_game_errors_explanation_adjustment_and_user_exit(self):
        steps = [{'text': text} for text in ('一马当先', '山清水秀', '什么意思', '难一点', '人山人海', '不玩了')]
        with Fixture(steps) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'user_exit')
            states = [window['state'] for window in fixture.robot.windows]
            self.assertEqual([state['total_successes'] for state in states], [0, 1, 1, 1, 1, 2])
            self.assertEqual([state['level'] for state in states], ['初级'] * 4 + ['中级'] * 2)
            self.assertEqual([state['last_idiom'] for state in states], ['一心一意'] + ['先声夺人'] * 4 + ['海阔天空'])
            self.assertEqual(fixture.effects.count, 2)
            self.assertEqual(fixture.session.game.snapshot()['phase'], 'ended')
            self.assertTrue(fixture.session.closed)
            self.assertFalse(fixture.voice.active)
            threads = fixture.effects.threads + [item['thread'] for item in fixture.media.played]
            self.assertEqual(set(threads), {threading.get_ident()})

    def test_idle_wake_then_valid_answer_continues_same_game(self):
        steps = [{'text': '一马当先'},
                 {'text': '人山人海', 'wait': 5, 'states': [(1, 0), (2, 3), (3, 1)]},
                 {'text': '不玩了'}]
        with Fixture(steps) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'user_exit')
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 2)
            starts = [request for request in fixture.models.requests if request['operation'] == 'start']
            self.assertEqual(len(starts), 1)
            answer = next(request for request in fixture.models.requests if request['utterance'] == '人山人海')
            self.assertEqual(answer['state']['last_idiom'], '先声夺人')
            self.assertEqual(answer['state']['total_successes'], 1)
            self.assertIn(0, [state for _, state in fixture.robot.states])

    def test_idle_still_times_out_at_original_120_second_deadline(self):
        with Fixture([{'states': [(30, 0)]}]) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(fixture.media.played[-1]['audio'], fixture.fallback_audio)
            deadline = fixture.robot.windows[0]['inactivity_deadline']
            self.assertGreaterEqual(fixture.media.played[-1]['at'], deadline)
            self.assertLess(fixture.media.played[-1]['at'], deadline + 1)
            self.assertEqual(fixture.effects.count, 0)

    def test_offline_timeout_uses_fallback_and_does_not_open_another_window(self):
        with Fixture(models=FixtureModels(fail_ending=True)) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(fixture.media.played[-1]['audio'], fixture.fallback_audio)
            self.assertLess(fixture.robot.windows[-1]['at'], fixture.media.played[-1]['at'])
            self.assertFalse(fixture.voice.active)

    def test_empty_asr_wake_and_noise_keep_old_deadline_and_do_not_speak(self):
        steps = [{'text': '', 'wait': 1}, {'text': '元仔元仔', 'wait': 1},
                 {'text': '无意义噪声', 'wait': 1}]
        with Fixture(steps) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(len({window['inactivity_deadline'] for window in fixture.robot.windows}), 1)
            # 前四个窗口仅有开局播报；噪声/唤醒不触发自己的回复。
            first_reminder = fixture.media.played[1]['at']
            self.assertGreaterEqual(first_reminder, fixture.robot.windows[0]['at'] + 10)
            self.assertEqual(len([p for p in fixture.media.played if p['at'] < first_reminder]), 1)
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)

    def test_failed_asr_prompts_retry_without_resetting_old_deadline(self):
        with Fixture([{'text': ProviderNetworkError('模拟断网'), 'wait': 4}]) as fixture:
            result = fixture.session.run()
            self.assertEqual(result['reason'], 'inactivity')
            self.assertEqual(len({window['inactivity_deadline'] for window in fixture.robot.windows}), 1)
            self.assertIn('刚才没有听清，不算答错，请再说一次。', fixture.models.spoken)
            self.assertEqual(fixture.session.game.snapshot()['wrong_attempts'], 0)
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)

    def test_tts_splits_at_100_characters_and_preserves_supported_sample_rate(self):
        for rate in (16000, 24000):
            with self.subTest(rate=rate):
                models = FixtureModels(rate=rate)
                data = synthesize(models, '字' * 205)
                self.assertEqual([len(text) for text in models.spoken], [100, 100, 5])
                with wave.open(io.BytesIO(data), 'rb') as audio:
                    self.assertEqual(audio.getframerate(), rate)
                    self.assertEqual(audio.getnchannels(), 1)
                    self.assertEqual(audio.getnframes(), rate // 20 * 3)

    def test_cancelled_model_result_cannot_later_speak_or_celebrate(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingModels(FixtureModels):
            worker = None
            def complete(self, messages):
                self.worker = threading.current_thread()
                entered.set()
                if not release.wait(2):
                    raise AssertionError('测试没有释放模型线程')
                return super().complete(messages)
        models = BlockingModels()
        with Fixture(models=models) as fixture:
            def cancel(robot):
                if entered.is_set():
                    raise Cancelled('模拟取消')
            fixture.robot.on_spin = cancel
            try:
                with self.assertRaises(Cancelled):
                    fixture.session.run()
            finally:
                release.set()
                if models.worker:
                    models.worker.join(1)
            self.assertEqual(fixture.media.played, [])
            self.assertEqual(fixture.effects.count, 0)
            self.assertEqual(fixture.session.game.snapshot()['phase'], 'ended')

    def test_timed_out_model_result_cannot_later_start_game(self):
        release = threading.Event()
        class BlockingModels(FixtureModels):
            worker = None
            def complete(self, messages):
                self.worker = threading.current_thread()
                if not release.wait(2):
                    raise AssertionError('测试没有释放模型线程')
                return super().complete(messages)
        models = BlockingModels()
        with Fixture(models=models, operation_timeout=.1) as fixture:
            try:
                with self.assertRaises(AudioError):
                    fixture.session.run()
            finally:
                release.set()
                if models.worker:
                    models.worker.join(1)
            self.assertEqual(fixture.media.played, [])
            self.assertEqual(fixture.session.game.snapshot()['last_idiom'], '')
            self.assertEqual(fixture.session.game.snapshot()['phase'], 'ended')

    def test_cancelled_answer_cannot_later_count_speak_or_celebrate(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingAnswer(FixtureModels):
            worker = None
            def complete(self, messages):
                if json.loads(messages[-1]['content'])['operation'] == 'respond':
                    self.worker = threading.current_thread()
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError('测试没有释放答案线程')
                return super().complete(messages)
        models = BlockingAnswer()
        with Fixture([{'text': '一马当先'}], models=models) as fixture:
            def cancel(robot):
                if entered.is_set():
                    raise Cancelled('模拟取消')
            fixture.robot.on_spin = cancel
            try:
                with self.assertRaises(Cancelled):
                    fixture.session.run()
            finally:
                release.set()
                if models.worker:
                    models.worker.join(1)
            self.assertEqual(len(fixture.media.played), 1)
            self.assertEqual(fixture.effects.count, 0)
            self.assertEqual(fixture.session.game.snapshot()['total_successes'], 0)
            self.assertEqual(fixture.session.game.snapshot()['last_idiom'], '一心一意')

    def test_closed_session_does_not_play_fallback_after_late_ending_tts(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingTTS(FixtureModels):
            worker = None
            def synthesize(self, text):
                self.worker = threading.current_thread()
                entered.set()
                if not release.wait(2):
                    raise AssertionError('测试没有释放 TTS 线程')
                return synthetic_wav()
        models = BlockingTTS()
        with Fixture(models=models) as fixture:
            def close(robot):
                if entered.is_set():
                    fixture.session.closed = True
            fixture.robot.on_spin = close
            try:
                with self.assertRaises(AudioError):
                    fixture.session.say(TIMEOUT_TEXT, ending=True)
            finally:
                release.set()
                if models.worker:
                    models.worker.join(1)
            self.assertEqual(fixture.media.played, [])

    def test_simulate_runs_all_cases_without_real_services_or_persistent_records(self):
        result = simulate()
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['simulated'])
        self.assertEqual(result['details']['game']['state']['total_successes'], 2)
        self.assertTrue(result['details']['offline_ending']['fallback_used'])
        self.assertEqual(result['details']['unchanged_deadline']['reason'], 'inactivity')


if __name__ == '__main__':
    unittest.main()
