"""Q1/T1 动作语义、独立入口及跨轮播放节点生命周期。"""
import ast
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Item
import unittest
from unittest.mock import Mock, patch

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]
effects_module = load_entry(ROOT, 'src/effects/music_effects.py')
facade_module = load_entry(ROOT, 'src/robot/facade.py')
device_module = load_entry(ROOT, 'src/robot/device.py')
AudioError = effects_module.AudioError


class Q1EffectsTests(unittest.TestCase):
    def setUp(self):
        self.effects = effects_module.Q1MusicEffects.__new__(effects_module.Q1MusicEffects)
        e = self.effects
        e.robot = Mock()
        e.motion_received_at = e.light_received_at = 10
        e.motion_state = Item(is_moving=False, is_fallen=False)
        e.action = Mock(return_value=Item(current_action=Item(value=1002), status=Item(value=100)))
        self.clock = patch.object(effects_module.time, 'monotonic', return_value=10)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_q1_accepts_stationary_humanoid_without_t1_form_fields(self):
        self.assertEqual(self.effects.require_ready(), 1002)
        self.assertEqual(self.effects.motion_name, 'Q_DANCE_A/B/C')
        self.assertEqual(self.effects.dance_action, 1005)

    def test_passive_and_t1_modes_are_never_treated_as_quad_prone(self):
        for action in (1, 101, 110, 201, 300, 1001, 1003, 1008):
            self.effects.action.return_value.current_action.value = action
            with self.subTest(action=action), self.assertRaises(AudioError):
                self.effects.require_ready()

    def test_q1_rejects_moving_fallen_held_and_stale_state(self):
        for name, value in [('is_moving', True), ('is_fallen', True),
                            ('held_state', Item(is_held=True)), ('lift_state', Item(is_lifted=True))]:
            self.effects.motion_state = Item(is_moving=False, is_fallen=False)
            setattr(self.effects.motion_state, name, value)
            with self.subTest(name=name), self.assertRaises(AudioError):
                self.effects.require_ready()
        self.effects.motion_state = Item(is_moving=False, is_fallen=False)
        self.effects.motion_received_at = 7
        with self.assertRaises(AudioError):
            self.effects.require_ready()

    def test_q1_preparation_never_sends_form_or_stand_commands(self):
        e = self.effects
        e.require_resource = Mock()
        e.set_stand = Mock()
        with tempfile.TemporaryDirectory() as directory:
            e.record = Path(directory) / 'restore.json'
            e.prepare()
            e.set_stand.assert_not_called()
            self.assertFalse(e.record.exists())

    def test_q1_preset_and_return_modes_use_q_series_ids(self):
        e = self.effects
        e.request = Mock(return_value=Item(source='', command=Item(action=Item(value=0), action_desc='')))
        e.last_control_at = 10
        e.rpc = Mock(return_value=Item(task_id=1))
        e.set_mode(1005)
        request = e.request.return_value
        self.assertEqual((request.command.action.value, request.command.action_desc), (1005, 'BIPED_WHOLE_BODY_CTRL'))
        e.action.return_value.current_action.value = 1005
        e.set_mode(1002, cleanup=True)
        self.assertEqual((request.command.action.value, request.command.action_desc), (1002, 'BIPED_WALK_RUN'))

    def test_q1_happy_and_reset_never_use_t1_emotion_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            e = self.effects
            e.record = Path(directory) / 'restore.json'
            e.original_light_state = e.last_owned_light_state = None
            e.stand_pending = e.restore_failed = e.emotion_submitted = False
            e.require_stand = Mock(return_value=(255, 0, 0, 0, 0))
            e.music_playing = lambda: True
            e.task_running_seen = True
            e.start, e.restore_stand, e.restore_light, e.play_emotion = Mock(), Mock(), Mock(), Mock()
            e.begin()
            e.play_emotion.assert_called_once_with(3003)
            e.stop_music()
            e.play_emotion.assert_called_with(3001, cleanup=True)
            self.assertFalse(e.record.exists())

    def test_missing_q1_preset_endpoint_cannot_fall_back_to_t1(self):
        e = self.effects
        client = Mock()
        client.wait_for_service.return_value = False
        e.clients = {'SetMcPresetMotion': client}
        with self.assertRaisesRegex(AudioError, 'SetMcPresetMotion'):
            e.require_resource()


class FacadeTests(unittest.TestCase):
    def test_two_calls_keep_the_same_ros_node_and_close_explicitly(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(facade_module, 'detect_device', return_value=Item(series='Q1')), \
                patch.object(facade_module, 'Robot') as robot, \
                patch.object(facade_module, 'AudioOutput') as output:
            output.return_value.robot = None
            facade = facade_module.RobotFacade(Path(directory))
            for _ in range(2):
                with facade.session(lambda: None):
                    facade.play_music('https://example.invalid/song')
            robot.assert_called_once()
            self.assertEqual(output.return_value.play.call_count, 2)
            robot.return_value.close.assert_not_called()
            facade.close()
            output.return_value.close.assert_called_once()

    def test_old_t1_restoration_record_blocks_new_operations(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(facade_module, 'detect_device', return_value=Item(series='Q1')), \
                patch.object(facade_module, 'Robot') as robot:
            record = Path(directory) / 'quad_effects_music.json'
            record.write_text('keep')
            with self.assertRaises(AudioError):
                facade_module.RobotFacade(Path(directory))
            robot.assert_not_called()
            self.assertEqual(record.read_text(), 'keep')

    def test_unknown_identity_is_rejected_before_ros_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in dict(series='unknown', sku='Q1', soc_index='0').items():
                (root / name).write_text(value)
            with self.assertRaises(ValueError):
                device_module.detect_device(root)


class IndependenceTests(unittest.TestCase):
    def test_business_and_robot_modules_do_not_import_primebuddy(self):
        for path in (ROOT / 'src').rglob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                         else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
                self.assertFalse(any(name == 'primebuddy' or name.startswith('primebuddy.') for name in names), str(path))

    def test_standalone_check_and_simulation_block_framework_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            for arguments in (['--check'], ['play', '--song', '小星星', '--simulate']):
                code = '''import importlib.abc,runpy,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname=='primebuddy' or fullname.startswith('primebuddy.'):
   raise AssertionError('framework dependency')
sys.meta_path.insert(0,Block())
sys.argv=''' + repr([str(ROOT / 'main.py'), *arguments]) + '''
runpy.run_path(sys.argv[0],run_name='__main__')
'''
                env = dict(os.environ, DIANGETAI_DATA=directory, PRIMEBUDDY_HOME=directory,
                           PRIMEBUDDY_SKILL_CONFIG=str(Path(directory) / 'config.yaml'))
                env.pop('PYTHONPATH', None)
                result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True,
                                        env=env, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
