"""QQ 登录只模拟云端接口；验证确认、取消、清理和框架引导衔接。"""
import asyncio
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from primebuddy.skill_manager import load_entry

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
                                       'qqmusic_api.modules.login_utils': login_utils}), \
                patch(self.module.__name__ + '.qr.render_qr'), patch.object(self.module, 'save_credential') as persist:
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
