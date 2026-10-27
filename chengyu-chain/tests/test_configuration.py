from copy import deepcopy
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.configuration import (DEFAULTS, ConfigError, ConfigureCancelled,
                                        configure, load_config, save_config, validate_config)
from chengyu_game.voices import VOICES


class UI:
    def __init__(self, confirmed=True, answers=None):
        self.confirmed, self.answers = confirmed, answers or {}
        self.messages, self.prompts, self.menus = [], [], []

    def header(self, title, subtitle=''):
        self.messages.append(title + ' ' + subtitle)

    def info(self, message):
        self.messages.append(message)

    def choose(self, label, choices, default=0):
        self.menus.append((label, choices, default))
        return self.answers.get(label, default)

    def ask(self, label, default='', secret=False):
        self.prompts.append((label, default, secret))
        return self.answers.get(label, default)

    def confirm(self, label):
        return self.confirmed


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'game/config.yaml'
        self.config = deepcopy(DEFAULTS)
        self.config['api_key'] = 'game-test-key'
        self.buddy = self.root / 'buddy'

    def write_buddy(self, provider='qianwen'):
        cfg = self.buddy / 'cfg'
        cfg.mkdir(parents=True)
        (cfg / 'config.yaml').write_text(yaml.safe_dump({
            role: {'model': provider + ':' + model} for role, model in self.config['models'].items()}))
        (cfg / 'providers.yaml').write_text(yaml.safe_dump({provider: {
            'api_host': DEFAULTS['api_host'], 'api_key': 'buddy-test-key'}}))

    def test_private_atomic_save_and_round_trip_preserve_other_settings(self):
        self.config['celebration'] = {'enabled': False}
        save_config(self.path, self.config)
        self.assertEqual(load_config(self.path), self.config)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertFalse(list(self.path.parent.glob('.chengyu-config-*')))

    def test_saved_game_takes_priority_without_reading_buddy(self):
        save_config(self.path, self.config)
        ui = UI()
        with patch('chengyu_game.configuration._buddy_defaults', side_effect=AssertionError('must not read')):
            result = configure(ui, self.path, self.buddy)
        self.assertEqual(result['api_key'], 'game-test-key')
        self.assertNotIn('game-test-key', str(ui.messages))
        self.assertTrue(next(item for item in ui.prompts if 'API Key' in item[0])[2])

    def test_first_install_imports_buddy_group_without_modifying_it(self):
        self.write_buddy()
        paths = list((self.buddy / 'cfg').iterdir())
        original = {p: p.read_bytes() for p in paths}
        configure(UI(), self.path, self.buddy)
        self.assertEqual(load_config(self.path)['api_key'], 'buddy-test-key')
        self.assertEqual({p: p.read_bytes() for p in paths}, original)

    def test_no_buddy_allows_manual_key(self):
        configure(UI(answers={'API Key': 'manual-test-key'}), self.path, self.buddy)
        self.assertEqual(load_config(self.path)['api_key'], 'manual-test-key')

    def test_selecting_tts_model_offers_compatible_default_voice(self):
        expected = {'qwen-audio-3.0-tts-plus': 'longanlingxin',
                    'qwen-audio-3.0-tts-flash': 'longanfengyue',
                    'qwen-audio-3.1-tts-plus': 'longanlingxin_v3.1',
                    'qwen-audio-3.1-tts-flash': 'longanlingxin_v3.1'}
        for model, voice in expected.items():
            with self.subTest(model=model):
                save_config(self.path, self.config)
                ui = UI()
                with patch.object(ui, 'choose', side_effect=lambda label, choices, default=0:
                                  choices.index(model) if label == 'TTS 模型' else default):
                    result = configure(ui, self.path, self.buddy)
                self.assertEqual(result['voice'], voice)
                self.assertFalse(any(p[0] == '自定义音色 ID' for p in ui.prompts))
                self.assertEqual(result['api_key'], 'game-test-key')

    def test_old_mismatched_default_can_be_corrected_without_reentering_key(self):
        self.config['models']['tts'] = 'qwen-audio-3.1-tts-plus'
        save_config(self.path, self.config)
        ui = UI()
        result = configure(ui, self.path, self.buddy)
        self.assertEqual(result['voice'], 'longanlingxin_v3.1')
        self.assertEqual(result['api_key'], 'game-test-key')
        self.assertTrue(any('音色' in text and '龙安灵心' in text for text in ui.messages))

    def test_unchanged_model_preserves_custom_voice(self):
        self.config['voice'] = 'custom-voice-id'
        save_config(self.path, self.config)
        ui = UI()
        self.assertEqual(configure(ui, self.path, self.buddy)['voice'], 'custom-voice-id')
        _, choices, default = next(menu for menu in ui.menus if menu[0] == 'TTS 音色')
        self.assertEqual(choices[default], '保留原自定义音色 · custom-voice-id')
        self.assertFalse(any(p[0] == '自定义音色 ID' for p in ui.prompts))

    def test_each_model_menu_saves_official_voice_id_behind_chinese_name(self):
        for model, voices in VOICES.items():
            self.config['models']['tts'] = model
            self.config['voice'] = voices[0][0]
            for index, (voice, label) in enumerate(voices):
                with self.subTest(model=model, voice=voice):
                    save_config(self.path, self.config)
                    ui = UI(answers={'TTS 音色': index})
                    result = configure(ui, self.path, self.buddy)
                    menu = next(menu for menu in ui.menus if menu[0] == 'TTS 音色')
                    self.assertEqual(menu[1][index], label)
                    self.assertEqual(menu[1][-1], '自定义音色 ID…')
                    self.assertEqual(result['voice'], voice)
                    self.assertNotIn(voice, label)

    def test_existing_nondefault_system_voice_is_selected_on_reconfigure(self):
        self.config['voice'] = 'longanlufeng'
        save_config(self.path, self.config)
        ui = UI()
        result = configure(ui, self.path, self.buddy)
        self.assertEqual(result['voice'], 'longanlufeng')
        self.assertEqual(next(menu[2] for menu in ui.menus if menu[0] == 'TTS 音色'), 1)

    def test_custom_option_accepts_id_and_edit_preserves_previous_default(self):
        self.config['voice'] = 'custom-existing-id'
        save_config(self.path, self.config)
        ui = UI(answers={'TTS 音色': len(VOICES[self.config['models']['tts']]) + 1,
                         '自定义音色 ID': 'custom-new-id'})
        result = configure(ui, self.path, self.buddy)
        self.assertEqual(result['voice'], 'custom-new-id')
        self.assertEqual(next(p[1] for p in ui.prompts if p[0] == '自定义音色 ID'), 'custom-existing-id')

    def test_model_change_offers_old_custom_but_defaults_to_matched_system_voice(self):
        self.config['voice'] = 'custom-existing-id'
        save_config(self.path, self.config)
        ui = UI(answers={'TTS 模型': 1})
        result = configure(ui, self.path, self.buddy)
        self.assertEqual(result['voice'], 'longanfengyue')
        menu = next(menu for menu in ui.menus if menu[0] == 'TTS 音色')
        self.assertEqual(menu[2], 0)
        self.assertIn('保留原自定义音色 · custom-existing-id', menu[1])

    def test_cancel_at_voice_selection_keeps_existing_file(self):
        save_config(self.path, self.config)
        before = self.path.read_bytes()
        ui = UI()

        def choose(label, choices, default=0):
            if label == 'TTS 音色':
                raise EOFError
            return default

        with patch.object(ui, 'choose', side_effect=choose), self.assertRaises(ConfigureCancelled):
            configure(ui, self.path, self.buddy)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_voice_choice_cannot_overwrite_configuration(self):
        save_config(self.path, self.config)
        before = self.path.read_bytes()
        for choice in (-1, 500, True, '0'):
            with self.subTest(choice=choice), self.assertRaises(ConfigError):
                configure(UI(answers={'TTS 音色': choice}), self.path, self.buddy)
            self.assertEqual(self.path.read_bytes(), before)

    def test_incompatible_buddy_provider_does_not_copy_its_key(self):
        self.write_buddy(provider='unsupported')
        ui = UI(answers={'API Key': 'manual-test-key'})
        configure(ui, self.path, self.buddy)
        self.assertEqual(load_config(self.path)['api_key'], 'manual-test-key')
        self.assertEqual(next(item[1] for item in ui.prompts if item[0] == 'API Key'), '')

    def test_cancel_preserves_existing_bytes(self):
        save_config(self.path, self.config)
        before = self.path.read_bytes()
        with self.assertRaises(ConfigureCancelled):
            configure(UI(confirmed=False), self.path, self.buddy)
        self.assertEqual(self.path.read_bytes(), before)

    def test_changing_host_does_not_silently_reuse_imported_key(self):
        self.write_buddy()
        ui = UI(answers={'千问北京地域 API Host': 'another.cn-beijing.maas.aliyuncs.com',
                         'API Key': 'new-workspace-key'})
        configure(ui, self.path, self.buddy)
        self.assertEqual(next(item[1] for item in ui.prompts if item[0] == 'API Key'), '')
        self.assertEqual(load_config(self.path)['api_key'], 'new-workspace-key')

    def test_interrupt_does_not_create_configuration(self):
        ui = UI()
        with patch.object(ui, 'ask', side_effect=KeyboardInterrupt), self.assertRaises(ConfigureCancelled):
            configure(ui, self.path, self.buddy)
        self.assertFalse(self.path.exists())

    def test_runtime_never_uses_environment_or_buddy_fallback(self):
        self.write_buddy()
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'environment-secret', 'PRIMEBUDDY_HOME': str(self.buddy)}):
            with self.assertRaises(ConfigError):
                load_config(self.path)
            self.config['api_key'] = ''
            with self.assertRaises(ConfigError):
                validate_config(self.config)

    def test_untrusted_host_and_unsupported_model_are_rejected(self):
        for host in ('evil.example', 'dashscope.aliyuncs.com.evil.example', 'https://dashscope.aliyuncs.com',
                     'dashscope.aliyuncs.com/path', 'dashscope.aliyuncs.com:443', 'user@dashscope.aliyuncs.com'):
            with self.subTest(host=host), self.assertRaises(ConfigError):
                validate_config(dict(self.config, api_host=host))
        self.assertEqual(validate_config(dict(self.config, api_host='workspace-123.cn-beijing.maas.aliyuncs.com'))['provider'], 'qianwen')
        with self.assertRaises(ConfigError):
            validate_config(dict(self.config, models=dict(self.config['models'], asr='anything')))

    def test_invalid_config_does_not_overwrite_saved_file(self):
        save_config(self.path, self.config)
        before = self.path.read_bytes()
        with self.assertRaises(ConfigError):
            save_config(self.path, dict(self.config, api_key='bad\nheader'))
        self.assertEqual(self.path.read_bytes(), before)

    def test_speech_rejects_unsupported_placeholder_without_echoing_value(self):
        with self.assertRaises(ConfigError) as raised:
            validate_config(dict(self.config, speech={'correct': 'private-speech-canary {api_key}'}))
        self.assertIn('correct', str(raised.exception))
        self.assertNotIn('private-speech-canary', str(raised.exception))
        self.assertNotIn('api_key', str(raised.exception))

    def test_reconfigure_adds_default_speech_and_preserves_custom_templates(self):
        for overrides in (None, {'win': '完成{level}挑战，太棒啦！'}):
            with self.subTest(overrides=overrides):
                old = deepcopy(self.config)
                old.pop('speech', None)
                if overrides is not None:
                    old['speech'] = overrides
                save_config(self.path, old)
                self.assertEqual(load_config(self.path), old)
                result = configure(UI(), self.path, self.buddy)
                expected = dict(DEFAULTS['speech'], **(overrides or {}))
                self.assertEqual(result['speech'], expected)
                self.assertEqual(load_config(self.path)['speech'], expected)
                self.assertEqual(set(expected), {'correct', 'win', 'win_next', 'lose'})

    def test_example_speech_matches_defaults_and_validates_without_key(self):
        example = Path(__file__).resolve().parents[1] / 'cfg/config.yaml'
        config = yaml.safe_load(example.read_text(encoding='utf-8'))
        self.assertEqual(config['speech'], DEFAULTS['speech'])
        self.assertEqual(validate_config(config, require_key=False), config)

    def test_invalid_speech_cannot_overwrite_saved_configuration(self):
        save_config(self.path, self.config)
        before = self.path.read_bytes()
        with self.assertRaises(ConfigError):
            save_config(self.path, dict(self.config, speech={'lose': 'private-speech-canary {api_key}'}))
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_yaml_keys_and_symlinks_are_rejected(self):
        self.path.parent.mkdir()
        self.path.write_text('provider: qianwen\nprovider: other\n')
        with self.assertRaises(ConfigError):
            load_config(self.path)
        target = self.root / 'target.yaml'
        target.write_text('unchanged')
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(ConfigError):
            save_config(self.path, self.config)
        self.assertEqual(target.read_text(), 'unchanged')


if __name__ == '__main__':
    unittest.main()
