"""安装向导保存配置后，云端失败仍应提供准确的重试说明。"""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
SPEC = importlib.util.spec_from_file_location('chengyu_cli_test', ROOT / 'main.py')
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)
from chengyu_game.configuration import DEFAULTS, load_config, save_config


class CliTests(unittest.TestCase):
    def test_motion_setting_is_persistent_but_never_commissions_or_calls_hardware(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = deepcopy(DEFAULTS)
            config['api_key'] = 'private-test-key'
            config['celebration'] = {'enabled': False, 'commissioned': False}
            save_config(data / 'config.yaml', config)
            for setting, expected in (('on', True), ('off', False)):
                output = io.StringIO()
                with patch('chengyu_game.runtime.play') as play, redirect_stdout(output):
                    code = CLI.main(['--data-dir', str(data), 'motion', setting])
                self.assertEqual(code, 0)
                play.assert_not_called()
                saved = load_config(data / 'config.yaml')
                self.assertIs(saved['celebration']['motion_enabled'], expected)
                self.assertIs(saved['celebration']['commissioned'], False)
                self.assertNotIn('private-test-key', output.getvalue())

    def test_play_passes_explicit_motion_override_only_for_this_run(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            config = deepcopy(DEFAULTS)
            config['api_key'] = 'private-test-key'
            save_config(data / 'config.yaml', config)
            original = (data / 'config.yaml').read_bytes()
            with patch('chengyu_game.runtime.play', return_value={'status': 'success'}) as play, redirect_stdout(io.StringIO()):
                self.assertEqual(CLI.main(['--data-dir', str(data), 'play', '--manage-agent', '--motion', 'off']), 0)
            self.assertIs(play.call_args.kwargs['motion_enabled'], False)
            self.assertEqual((data / 'config.yaml').read_bytes(), original)

    def test_configure_tts_failure_preserves_config_and_explains_retry_without_key(self):
        ui = Mock()
        ui.choose.return_value = 0
        ui.ask.side_effect = lambda label, default='', secret=False: 'private-test-key' if secret else default
        ui.confirm.return_value = True
        opener = Mock(open=Mock(side_effect=urllib.error.HTTPError(
            'https://private.example/key', 400, 'private-test-key', {}, None)))
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            output = io.StringIO()
            with patch.object(CLI, 'Console', return_value=ui), \
                    patch('chengyu_game.configuration._buddy_defaults', return_value=None), \
                    patch('urllib.request.build_opener', return_value=opener), redirect_stdout(output):
                code = CLI.main(['--data-dir', str(data), 'configure'])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(result['resources_released'])
            self.assertIn('配置已保存', result['message'])
            self.assertIn('结束播报 TTS', result['message'])
            self.assertIn('prepare-audio', result['message'])
            self.assertNotIn('private-test-key', output.getvalue())
            self.assertEqual(load_config(data / 'config.yaml')['api_key'], 'private-test-key')
            self.assertFalse((data / 'ending.wav').exists())
        self.assertEqual(opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
