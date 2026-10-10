"""通过真实 skill configure 命令保存技能配置，保留独立登录数据。"""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from primebuddy import skill_manager
from primebuddy.__main__ import main
from primebuddy.settings import skill_config, write_json

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
            package = home / 'packages/diangetai-test'
            shutil.copytree(ROOT / 'src', package / 'src', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copy(ROOT / 'manifest.json', package)
            shutil.copy(ROOT / 'SKILL.md', package)
            write_json(home / 'skills.json', {'diangetai': {'path': package.name,
                       'version': '0.2.0', 'ready': True, 'enabled': True}})
            write_json(home / 'data/diangetai/config.json', {'play_seconds': 30, 'music_effects': False})
            credential = home / 'data/diangetai/credential.json'
            credential.write_bytes(b'unchanged-test-login')
            load = skill_manager.load_entry
            def load_with_login(root, entry):
                module = load(root, entry)
                if entry == 'src/setup.py':
                    module.check_login = AsyncMock(return_value=True)
                    module.login = AsyncMock(side_effect=AssertionError('不应再次扫码'))
                return module
            with patch('sys.argv', ['primebuddy', 'skill', 'configure', 'diangetai']), \
                    patch('primebuddy.wizard.Wizard', return_value=ui), \
                    patch.object(skill_manager, 'load_entry', side_effect=load_with_login), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(ui.seen_default, '30')
            self.assertEqual(skill_config('diangetai'), {'play_seconds': 37, 'music_effects': True})
            self.assertTrue(skill_manager.records()['diangetai']['ready'])
            self.assertEqual(credential.read_bytes(), b'unchanged-test-login')
