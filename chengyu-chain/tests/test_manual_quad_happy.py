"""单次开发验收工具的离线验证；所有原厂接口均为 fake。"""
from contextlib import contextmanager, redirect_stderr
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_celebration import FakeRobot, messages, fake_expression
from chengyu_game.robot.device import Device
from chengyu_game.support import AudioError, Cancelled, SetupError, exclusive

SPEC = importlib.util.spec_from_file_location('quad_happy_trial', Path(__file__).parent / 'manual/t1_quad_happy_once.py')
TRIAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAL)


class ManualQuadHappyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.robot = FakeRobot(102)
        self.robot.closed = False
        self.robot.close = lambda: setattr(self.robot, 'closed', True)
        self.identity = Device('T1', 'T1-2X', '0')
        self.frontend_held = False
        modules = messages()
        modules['aimdk_msgs.msg'].McAction.QUADRUPED_LOCOMOTION_DEFAULT = 102
        original_read = Path.read_text
        @contextmanager
        def frontend():
            self.frontend_held = True
            try:
                yield
            finally:
                self.frontend_held = False
        def factory(runtime, checkpoint):
            self.assertTrue(self.frontend_held)
            with self.assertRaises(SetupError):
                with exclusive(runtime):
                    self.fail('command lock must remain held')
            return self.robot
        for patcher in (patch.dict(sys.modules, modules),
                        patch('chengyu_game.robot.celebration.detect_device', lambda: self.identity),
                        patch('chengyu_game.robot.celebration.load_quad_route',
                              lambda runtime, initial: {'enter': [114], 'return': [initial]}),
                        patch('chengyu_game.robot.celebration.play_expression', fake_expression),
                        patch('chengyu_game.robot.celebration.time.monotonic', lambda: self.robot.now),
                        patch.object(Path, 'read_text', lambda path, *args, **kwargs: 'boot-test' if str(path) ==
                                     '/proc/sys/kernel/random/boot_id' else original_read(path, *args, **kwargs)),
                        patch.object(TRIAL, 'frontend_lock', frontend),
                        patch.object(TRIAL, 'Robot', side_effect=factory)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_trial(self):
        return TRIAL.run_once(self.runtime, confirmed=True)

    def test_one_trial_returns_to_original_standing_action_and_resets_face(self):
        dispatched_at = []
        original = self.robot.rpc
        def timed_rpc(name, request):
            if name == 'SetMcAction':
                dispatched_at.append(self.robot.now)
            return original(name, request)
        self.robot.rpc = timed_rpc
        for initial in (101, 102):
            with self.subTest(initial=initial):
                dispatched_at.clear()
                self.robot.action, self.robot.writes, self.robot.faces = initial, [], []
                result = self.run_trial()
                self.assertEqual(result['motion']['status'], 'completed')
                self.assertEqual(self.robot.writes, [('action', 114), ('action', initial)])
                self.assertGreaterEqual(dispatched_at[1] - dispatched_at[0], 1.2)
                self.assertLess(dispatched_at[1] - dispatched_at[0], 1.6)
                self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
                self.assertFalse((self.runtime / 'effects.json').exists())
                self.assertTrue(self.robot.closed)
                self.assertEqual({path.name for path in self.runtime.iterdir()}, {'command.lock'})

    def test_missing_confirmation_or_locks_never_creates_robot(self):
        with self.assertRaises(SetupError):
            TRIAL.run_once(self.runtime)
        with exclusive(self.runtime), self.assertRaises(SetupError):
            self.run_trial()
        with patch.object(TRIAL, 'frontend_lock', side_effect=SetupError('busy')), self.assertRaises(SetupError):
            self.run_trial()
        TRIAL.Robot.assert_not_called()

    def test_cli_requires_explicit_onsite_confirmation(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            TRIAL.main(['--runtime-dir', str(self.runtime)])
        self.assertEqual(caught.exception.code, 2)
        TRIAL.Robot.assert_not_called()

    def test_current_firmware_step_needs_its_own_explicit_trial_scope(self):
        with patch('chengyu_game.robot.celebration.load_quad_route',
                   return_value={'enter': [121, 114], 'return': [102]}):
            with self.assertRaisesRegex(SetupError, '原地踏步'):
                self.run_trial()
            self.assertEqual(self.robot.writes, [])
            self.assertEqual(self.robot.faces, [])
            result = TRIAL.run_once(self.runtime, confirmed=True, include_step=True)
        self.assertEqual(result['motion']['status'], 'completed')
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 114), ('action', 102)])
        self.assertFalse((self.runtime / 'effects.json').exists())

    def test_mode_whitelist_rejects_unapproved_transitions(self):
        trial = TRIAL.QuadHappyTrial(self.robot, self.runtime)
        trial.record = {'motion': {'initial_action': 102}}
        for action, stage, cleanup in ((300, 'mode', False), (101, 'standing', False),
                                       (101, 'return', True), (114, 'return', True)):
            with self.subTest(action=action, stage=stage), self.assertRaises(SetupError):
                trial._mode(action, stage, cleanup)
        self.assertEqual(self.robot.writes, [])

    def test_startup_trial_plays_notice_then_stands_and_celebrates_once(self):
        self.robot.action, self.robot.form = 110, 1
        media = Mock()
        media.play.side_effect = lambda audio: self.assertEqual(self.robot.writes, [])
        with patch('chengyu_game.configuration.load_config', return_value={}), \
                patch('chengyu_game.providers.Models'), \
                patch('chengyu_game.session.synthesize', return_value=b'notice') as tts, \
                patch('chengyu_game.robot.native_media.NativeMedia', return_value=media), \
                patch('chengyu_game.robot.celebration.load_quad_route',
                      return_value={'enter': [121, 114], 'return': [121, 101]}):
            result = TRIAL.run_once(self.runtime, confirmed=True, include_stand=True,
                                    include_step=True, config_path=self.runtime / 'private.yaml')
        self.assertEqual(result['motion']['status'], 'completed')
        self.assertEqual(tts.call_args.args[1], '请确认四周安全，我要站起来了。')
        media.play.assert_called_once_with(b'notice')
        media.close.assert_called_once()
        self.assertEqual(self.robot.writes, [('action', 101), ('action', 121), ('action', 114),
                                             ('action', 121), ('action', 101)])
        self.assertEqual({p.name for p in self.runtime.iterdir()}, {'command.lock'})

    def test_startup_trial_without_notice_configuration_never_connects(self):
        with self.assertRaisesRegex(SetupError, '提示'):
            TRIAL.run_once(self.runtime, confirmed=True, include_stand=True)
        TRIAL.Robot.assert_not_called()

    def test_pending_game_record_blocks_trial(self):
        (self.runtime / 'media.json').write_text('{}')
        with self.assertRaises(SetupError):
            self.run_trial()
        TRIAL.Robot.assert_not_called()

    def test_passive_seated_biped_or_wrong_form_never_moves_or_shows_face(self):
        for action, form in ((1, 1), (110, 1), (111, 1), (201, 2), (300, 2), (102, 2)):
            with self.subTest(action=action, form=form):
                self.robot.action, self.robot.form = action, form
                with self.assertRaises(SetupError):
                    self.run_trial()
                self.assertEqual(self.robot.writes, [])
                self.assertEqual(self.robot.faces, [])

    def test_wrong_identity_or_runtime_enum_never_moves(self):
        for identity in (Device('Q1', 'Q1-1X', '0'), Device('T1', 'another', '0')):
            self.identity = identity
            with self.assertRaises(SetupError):
                self.run_trial()
        self.identity = Device('T1', 'T1-2X', '0')
        sys.modules['aimdk_msgs.msg'].McAction.QUADRUPED_LOCOMOTION_PLEASURE = 999
        with self.assertRaises(SetupError):
            self.run_trial()
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(self.robot.faces, [])

    def test_charging_off_power_or_lifted_refuses_without_expression(self):
        for field, value in (('battery_status', 1), ('power_state', 2), ('lifted', True)):
            previous = getattr(self.robot, field)
            setattr(self.robot, field, value)
            with self.subTest(field=field), self.assertRaises(SetupError):
                self.run_trial()
            setattr(self.robot, field, previous)
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(self.robot.faces, [])

    def test_cancel_after_start_restores_owned_action_and_face(self):
        self.robot.on_spin = lambda robot: setattr(robot, 'cancel', bool(robot.writes))
        with self.assertRaises(Cancelled):
            self.run_trial()
        self.assertEqual(self.robot.writes, [('action', 114), ('action', 102)])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse((self.runtime / 'effects.json').exists())

    def test_unknown_write_is_not_repeated_and_retains_record(self):
        self.robot.fail = 'SetMcAction'
        with self.assertRaises(AudioError):
            self.run_trial()
        self.assertEqual(self.robot.writes, [('action', 114)])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertTrue((self.runtime / 'effects.json').exists())

    def test_external_takeover_is_not_overwritten(self):
        def takeover(robot):
            if robot.writes:
                robot.task_id = 999
        self.robot.on_spin = takeover
        with self.assertRaises(AudioError):
            self.run_trial()
        self.assertEqual(self.robot.writes, [('action', 114)])
        self.assertTrue((self.runtime / 'effects.json').exists())


if __name__ == '__main__':
    unittest.main()
