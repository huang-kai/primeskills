"""点歌配置、扫码登录和独立凭据保留。"""
from pathlib import Path
import unittest
from unittest.mock import Mock
from primebuddy.config.storage import SetupError
from primebuddy.skills.manager import load_entry
import asyncio
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
import io
import os
import shutil
from primebuddy.skills import manager as skill_manager
from primebuddy.cli.commands import main
from primebuddy.config.storage import skill_config, write_config


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/configuration/playback.py')

    def test_old_installations_keep_full_song_until_reconfigured(self):
        self.assertEqual(self.module.playback_seconds({}), 0)

    def test_invalid_duration_is_rejected_before_any_playback(self):
        for value in (-1, 601, True, 1.5, '30', None):
            with self.subTest(value=value), self.assertRaises(SetupError):
                self.module.playback_seconds({'play_seconds': value})

    def test_setup_retries_invalid_input_and_preserves_other_settings(self):
        ui = Mock()
        ui.ask.side_effect = ['abc', '-1', '601', '1.5', '30']
        self.assertEqual(self.module.configure_playback(ui, {'keep': 'yes'}, 60),
                         {'keep': 'yes', 'play_seconds': 30})
        self.assertEqual(ui.info.call_count, 4)

    def test_reconfiguration_displays_saved_value_and_allows_full_song(self):
        ui = Mock()
        ui.ask.return_value = '0'
        config = {'play_seconds': 30}
        result = self.module.configure_playback(ui, config, 60)
        self.assertEqual(ui.ask.call_args.args[1], '30')
        self.assertEqual(result['play_seconds'], 0)
        self.assertEqual(config['play_seconds'], 30)


ROOT = Path(__file__).resolve().parents[1]


class UI:
    def __init__(self, choice=1, confirm=True):
        self.choice, self.confirmed = choice, confirm
        self.messages = []
    def info(self, message):
        self.messages.append(message)
    def choose(self, *args):
        return self.choice
    def confirm(self, *args, **kwargs):
        return self.confirmed
    def ask(self, label, default=''):
        return default


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/setup.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.ui = UI()

    def test_valid_account_reuses_config_without_new_qr(self):
        with patch.object(self.module, 'check_login', AsyncMock(return_value=True)), patch.object(self.module, 'login') as login:
            self.assertEqual(self.module.setup(self.ui, self.directory, {'keep': 1}), {'keep': 1, 'play_seconds': 60, 'music_effects': True})
        login.assert_not_called()

    def test_skip_login_returns_pending(self):
        with patch.object(self.module, 'check_login', AsyncMock(return_value=False)):
            self.assertIsNone(self.module.setup(UI(confirm=False), self.directory, {}))

    def test_network_error_can_be_deferred_and_secret_is_not_printed(self):
        with patch.object(self.module, 'check_login', AsyncMock(side_effect=RuntimeError('secret'))):
            self.assertIsNone(self.module.setup(self.ui, self.directory, {}))
        self.assertNotIn('secret', str(self.ui.messages))

    def run_qr(self, expired=False, error=None):
        image = self.directory / 'qr.png'
        def save(directory):
            image.write_bytes(b'test-image')
            return image
        credential = object()
        session = SimpleNamespace(get_qrcode=AsyncMock(return_value=SimpleNamespace(save=save)),
                                  wait_qrcode_login=AsyncMock(return_value=credential, side_effect=error))
        api = ModuleType('qqmusic_api')
        client = SimpleNamespace(login=SimpleNamespace(check_expired=AsyncMock(return_value=expired)))
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return client
            async def __aexit__(self, *args): pass
        api.Client = Client
        models = ModuleType('qqmusic_api.models.login')
        models.QRLoginType = SimpleNamespace(QQ='qq')
        login_utils = ModuleType('qqmusic_api.modules.login_utils')
        login_utils.QRCodeLoginSession = lambda *args, **kwargs: session
        with patch.dict('sys.modules', {'qqmusic_api': api, 'qqmusic_api.models.login': models,
                                       'qqmusic_api.modules.login_utils': login_utils}),\
                patch(self.module.__name__ + '.configuration.qr.render_qr'), patch.object(self.module, 'save_credential') as persist:
            try:
                asyncio.run(self.module.login(self.ui, self.directory))
            except (ValueError, TimeoutError, asyncio.CancelledError):
                self.assertFalse(image.exists())
                persist.assert_not_called()
                raise
            persist.assert_called_once_with(credential, self.directory)
        self.assertTrue(any('扫描二维码' in message for message in self.ui.messages))
        self.assertFalse(image.exists())

    def test_scan_then_server_confirmation_persists_credential_and_cleans_qr(self):
        self.run_qr()

    def test_expired_credential_is_not_saved(self):
        with self.assertRaises(ValueError):
            self.run_qr(expired=True)

    def test_timeout_cleans_qr_and_does_not_save(self):
        with self.assertRaises(TimeoutError):
            self.run_qr(error=TimeoutError())

    def test_cancellation_cleans_qr_and_does_not_save(self):
        with self.assertRaises(asyncio.CancelledError):
            self.run_qr(error=asyncio.CancelledError())


ROOT = Path(__file__).resolve().parents[1]


class QrTests(unittest.TestCase):
    def setUp(self):
        self.qr = load_entry(ROOT, 'src/configuration/qr.py')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name) / 'qr.png'
        self.image.write_bytes(b'test')
        self.ui = SimpleNamespace(info=Mock())

    def test_cleanup_even_when_cancelled_during_render(self):
        with patch.object(self.qr, 'render_qr', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            with self.qr.show_qr(self.ui, self.image):
                self.fail('取消后不应继续登录')
        self.assertFalse(self.image.exists())

    def test_fallback_keeps_private_image_during_login_and_removes_after(self):
        with patch.object(self.qr, 'render_qr', side_effect=ImportError):
            with self.qr.show_qr(self.ui, self.image):
                self.assertTrue(self.image.exists())
                self.assertEqual(self.image.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.image.exists())
        self.assertIn(str(self.image), str(self.ui.info.call_args_list))

    def test_login_failure_still_cleans_image(self):
        with patch.object(self.qr, 'render_qr'), self.assertRaises(TimeoutError):
            with self.qr.show_qr(self.ui, self.image):
                raise TimeoutError
        self.assertFalse(self.image.exists())


ROOT = Path(__file__).resolve().parents[1]


class ReconfigureTests(unittest.TestCase):
    def test_command_reuses_login_and_updates_configuration(self):
        class UI:
            def info(self, message): pass
            def ask(self, label, default):
                self.seen_default = default
                return '37'
            def confirm(self, label, default=True): return True
        ui = UI()
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, PRIMEBUDDY_HOME=temp):
            home = Path(temp)
            package = home / 'skills/diangetai'
            shutil.copytree(ROOT / 'src', package / 'src', ignore=shutil.ignore_patterns('__pycache__'))
            for directory in ('cfg', 'docs'):
                shutil.copytree(ROOT / directory, package / directory)
            for filename in ('SKILL.md', 'requirements.txt'):
                shutil.copy(ROOT / filename, package / filename)
            write_config(home / 'cfg/skills.yaml', {'diangetai': {'path': package.name,
                       'version': '0.2.0', 'ready': True, 'enabled': True}})
            write_config(home / 'data/diangetai/config.yaml', {'play_seconds': 30, 'music_effects': False})
            credential = home / 'data/diangetai/credential.json'
            credential.write_bytes(b'unchanged-test-login')
            load = skill_manager.load_entry
            def load_with_login(root, entry):
                module = load(root, entry)
                if entry == 'src/setup.py':
                    module.check_login = AsyncMock(return_value=True)
                    module.login = AsyncMock(side_effect=AssertionError('不应再次扫码'))
                return module
            with patch('sys.argv', ['primebuddy', 'skill', 'configure', 'diangetai']),\
                    patch('primebuddy.cli.wizard.Wizard', return_value=ui),\
                    patch.object(skill_manager, 'load_entry', side_effect=load_with_login), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(ui.seen_default, '30')
            self.assertEqual(skill_config('diangetai'), {'play_seconds': 37, 'music_effects': True})
            self.assertTrue(skill_manager.load_skill_records()['diangetai']['ready'])
            self.assertEqual(credential.read_bytes(), b'unchanged-test-login')
