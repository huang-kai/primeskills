"""独立 CLI 与真实目标确认规则；仅使用模拟 MCP。"""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]
skill = load_entry(ROOT, 'src/skill.py')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.args = dict(power=True, scope='device', room='客厅', device='天际线灯')
        self.snapshot = {'status': 'success', 'rooms': ['客厅'],
                         'devices': [{'name': '天际线灯', 'room': '客厅'}]}

    def test_exact_request_needs_no_confirmation_and_never_controls(self):
        with patch.object(skill, 'context', return_value=self.snapshot), \
                patch.object(skill.mcp_client, 'McpLightClient') as writer:
            result = skill.preview_power(**self.args, text='打开客厅的天际线灯', config={'house_id': 'test'})
        self.assertIsNone(result['question'])
        self.assertEqual(result['description'], '正在打开客厅的天际线灯')
        writer.assert_not_called()

    def test_corrected_name_and_added_room_require_confirmation(self):
        for text in ('打开客厅的天气线灯', '打开天际线灯'):
            with patch.object(skill, 'context', return_value=self.snapshot):
                result = skill.preview_power(**self.args, text=text, config={'house_id': 'test'})
            self.assertTrue(result['question'])
            self.assertIn('打开客厅的天际线灯', result['question'])

    def test_unknown_and_duplicate_targets_are_rejected(self):
        for devices in ([], self.snapshot['devices'] * 2):
            snapshot = dict(self.snapshot, devices=devices)
            with patch.object(skill, 'context', return_value=snapshot), self.assertRaises(ValueError):
                skill.preview_power(**self.args, text='打开客厅的天际线灯', config={'house_id': 'test'})

    def test_home_scope_requires_explicit_all_lights_request(self):
        args = dict(power=False, scope='home', room='', device='')
        self.assertIsNone(skill.confirmation(args, '关闭全屋灯光', self.snapshot))
        self.assertTrue(skill.confirmation(args, '关灯', self.snapshot))

    def test_preflight_checks_configuration_and_arguments_without_mcp(self):
        with patch.object(skill.mcp_client, 'LocalMcp') as reader:
            self.assertEqual(skill.check_power(**self.args, config={'house_id': 'test'})['status'], 'success')
            for args, config in ((dict(self.args, device=''), {'house_id': 'test'}),
                                 (dict(self.args, device='x' * 81), {'house_id': 'test'}),
                                 (self.args, {})):
                with self.assertRaises(ValueError):
                    skill.check_power(**args, config=config)
            reader.assert_not_called()


class IndependenceTests(unittest.TestCase):
    def test_business_and_entrypoint_do_not_import_framework(self):
        for path in [ROOT / 'main.py', *(ROOT / 'src').rglob('*.py')]:
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([node.module or ''] if isinstance(node, ast.ImportFrom) else
                         [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                self.assertFalse(any(name == 'primebuddy' or name.startswith('primebuddy.') for name in names), str(path))

    def test_standalone_check_simulation_and_preview_without_framework(self):
        with tempfile.TemporaryDirectory() as directory:
            for args in (['--check'], ['set-power', '--power', 'true', '--scope', 'room', '--room', '厨房', '--simulate'],
                         ['preview-power', '--power', 'off', '--scope', 'home', '--text', '关灯', '--simulate']):
                code = '''import importlib.abc,runpy,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='primebuddy' or fullname.startswith('primebuddy.'):
   raise AssertionError('framework dependency')
sys.meta_path.insert(0,Block())
sys.argv=''' + repr([str(ROOT / 'main.py'), '--data-dir', directory, '--config', str(Path(directory) / 'empty.yaml'), *args]) + '''
runpy.run_path(sys.argv[0],run_name='__main__')
'''
                result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                parsed = json.loads(result.stdout)
                self.assertEqual(parsed['status'], 'success')
                if args[0] == 'preview-power':
                    self.assertTrue(parsed['question'])
