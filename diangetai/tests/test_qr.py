"""二维码是点歌台登录流程的私有实现，只要求通用 ui.info。"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from primebuddy.skill_manager import load_entry

ROOT = Path(__file__).resolve().parents[1]


class QrTests(unittest.TestCase):
    def setUp(self):
        self.qr = load_entry(ROOT, 'src/qr.py')
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
