from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.speech import DEFAULT_SPEECH, SpeechError, render_speech, validate_speech


class SpeechTests(unittest.TestCase):
    def test_custom_template_renders_and_missing_event_uses_default(self):
        speech = validate_speech({'correct': '真棒，已经接对{successes}次，我接{next_idiom}。'})
        self.assertEqual(render_speech(speech, 'correct', successes=1, next_idiom='风和日丽'),
                         '真棒，已经接对1次，我接风和日丽。')
        self.assertEqual(render_speech(speech, 'exit'), DEFAULT_SPEECH['exit'])
        self.assertEqual(render_speech(None, 'win', level='初级', target=5),
                         '太棒了！你已经接对5次，赢得了初级挑战！')

    def test_optional_overrides_and_validated_copy(self):
        self.assertEqual(validate_speech(), {})
        self.assertEqual(validate_speech({}), {})
        original = {'lose': '我们下次再试吧。'}
        result = validate_speech(original)
        self.assertEqual(result, original)
        result['lose'] = '下次加油。'
        self.assertEqual(original['lose'], '我们下次再试吧。')

    def test_unknown_keys_and_non_objects_do_not_echo_untrusted_content(self):
        for value in ([], 'private-speech-canary', 1,
                      {'private-speech-canary': 'unknown'}, {1: 'unknown'}):
            with self.subTest(value=value), self.assertRaises(SpeechError) as raised:
                validate_speech(value)
            self.assertNotIn('private-speech-canary', str(raised.exception))

    def test_invalid_values_are_rejected_with_only_the_known_key(self):
        for value in (None, 5, [], {}, '', '   ', 'private-speech-canary\n',
                      'private-speech-canary\x7f', 'private-speech-canary\u200b', '字' * 401):
            with self.subTest(value=value), self.assertRaises(SpeechError) as raised:
                validate_speech({'correct': value})
            self.assertEqual(str(raised.exception), '播音词 correct 无效。')
        self.assertEqual(validate_speech({'correct': '字' * 400}), {'correct': '字' * 400})

    def test_only_declared_simple_fields_are_accepted(self):
        for template in ('{api_key}', '{next_idiom.upper}', '{next_idiom[0]}', '{}', '{0}',
                         '{next_idiom!r}', '{next_idiom!s}', '{next_idiom:>4}',
                         '{next_idiom:}', '{next_idiom:{target}}', '{', '}'):
            with self.subTest(template=template), self.assertRaises(SpeechError) as raised:
                validate_speech({'correct': 'private-speech-canary ' + template})
            self.assertEqual(str(raised.exception), '播音词 correct 无效。')
        with self.assertRaises(SpeechError):
            validate_speech({'win': '{required_char}'})
        with self.assertRaises(SpeechError):
            validate_speech({'lose': '{target}'})

    def test_literal_braces_and_repeated_fields_are_supported(self):
        speech = validate_speech({'correct': '{{示例}}我接{next_idiom}，请从{next_idiom}继续。'})
        self.assertEqual(render_speech(speech, 'correct', next_idiom='风和日丽'),
                         '{示例}我接风和日丽，请从风和日丽继续。')

    def test_all_internal_default_events_render(self):
        expected = {'hint', 'opening', 'resume', 'correct', 'correction', 'win', 'win_next',
                    'win_all_next', 'retry_next', 'lose', 'timeout_loss', 'assist', 'assist_error',
                    'exit', 'inactivity', 'provider_error', 'asr_retry', 'start_error', 'restart_error',
                    'not_started', 'judge_retry', 'round_over', 'current_idiom', 'level_same',
                    'level_changed', 'not_idiom', 'uncertain', 'wrong_sound', 'wrong_limit',
                    'motion_unavailable'}
        self.assertEqual(set(DEFAULT_SPEECH), expected)
        speech = {}
        values = dict(required_char='意', level='初级', target=5, last_idiom='一心一意',
                      successes=1, next_idiom='风和日丽', answer='意气风发', next_level='中级',
                      wrong_attempts=1)
        for key in expected:
            with self.subTest(key=key):
                text = render_speech(speech, key, **values)
                self.assertTrue(text.strip())
                self.assertNotIn('{', text)
        self.assertIn('本局已接对1次', render_speech(speech, 'correct', **values))
        self.assertIn('目标是5次', render_speech(speech, 'correct', **values))
        self.assertIn('提高难度', render_speech(speech, 'win_next', **values))
        self.assertIn('继续', render_speech(speech, 'win_next', **values))
        self.assertEqual(render_speech(speech, 'wrong_limit'), '三次机会用完啦。')

    def test_render_errors_do_not_echo_templates_or_supplied_values(self):
        speech = validate_speech({'win': 'private-speech-canary {level} {target}'})
        with self.assertRaises(SpeechError) as raised:
            render_speech(speech, 'win', level='private-level-canary')
        self.assertEqual(str(raised.exception), '播音词 win 无法填入。')
        with self.assertRaises(SpeechError) as raised:
            render_speech(speech, 'private-key-canary')
        self.assertNotIn('private-key-canary', str(raised.exception))

    def test_only_four_events_can_be_customized(self):
        allowed = {'correct', 'win', 'win_next', 'lose'}
        for key in DEFAULT_SPEECH:
            with self.subTest(key=key):
                if key in allowed:
                    self.assertEqual(validate_speech({key: DEFAULT_SPEECH[key]}),
                                     {key: DEFAULT_SPEECH[key]})
                else:
                    with self.assertRaises(SpeechError):
                        validate_speech({key: DEFAULT_SPEECH[key]})


if __name__ == '__main__':
    unittest.main()
