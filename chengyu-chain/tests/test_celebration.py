from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from chengyu_game.robot.celebration import Celebration
from chengyu_game.robot.device import Device
from chengyu_game.robot.transport import Robot
from chengyu_game.support import AudioError, Cancelled, SetupError, read_json, write_json


def request():
    return NS(header=NS(header=NS()), request=NS(header=NS()), command=NS(action=NS()),
              motion=NS(), area=NS(), source='', type=0, model='', resource='', interrupt=False,
              operation=0, MIMIC_QY=1, EXECUTION=2)


def fake_expression(robot, client, request, series, *, cleanup=False, before_submit=None):
    return robot.call(client, request, cancellable=not cleanup, before_submit=before_submit)


def messages():
    names = ('PlayEmotion', 'GetMcAction', 'GetMcMotions', 'SetMcAction',
             'SetMcPresetMotion', 'GetMcPresetMotionState', 'SetMcMotion', 'GetJointPowerState')
    services = NS(**{name: type(name, (), {'Request': staticmethod(request)}) for name in names})
    services.GetJointPowerState.Response = NS(POWER_ON=1, POWER_OFF=2)
    services.PlayEmotion.Response = lambda: NS(header=NS(header=NS(code=0),
        status=NS(value=0, reason=0, FAILURE=2, INVALID=5, NOT_READY=100)))
    return {'aimdk_msgs': NS(), 'aimdk_msgs.srv': services,
            'aimdk_msgs.msg': NS(McCommonState=object, Bms=object,
                                McAction=NS(QUADRUPED_LOCOMOTION_PLEASURE=114,
                                            QUADRUPED_LOCOMOTION_STEP=121,
                                            QUADRUPED_MIMIC=119,
                                            QUADRUPED_STAND_DEFAULT=101,
                                            QUADRUPED_GET_DOWN_DEFAULT=110,
                                            QUADRUPED_SIT_DOWN_DEFAULT=111),
                                McPresetMotion=NS(DEFAULT_POS=0, BOTH_HANDS_ACTION_HAPPY=2002,
                                                  Q_SHOW_HAPPINESS=3005, HUMANOID_SAD=2033))}


class FakeClient:
    def __init__(self, robot, name):
        self.robot, self.name = robot, name

    def service_is_ready(self):
        return True

    def call_async(self, request):
        response = self.robot.rpc(self.name, request)
        return NS(done=lambda: True, result=lambda: response)


class FakeRobot:
    validate_response = staticmethod(Robot.validate_response)

    def __init__(self, action=300):
        self.now, self.action, self.task_id = 1000, action, 7
        self.form, self.moving = (1 if action in (101, 102) else 2), False
        self.action_status, self.switch_ready = 100, True
        self.fallen = self.lifted = self.held = False
        self.battery_status, self.battery_missing = 0, False
        self.callbacks, self.writes, self.faces, self.destroyed = [], [], [], []
        self.preset_checks, self.fail, self.on_spin = {}, None, None
        self.cancel = False
        self.mimic_checks, self.mimic_entry, self.mimic_completes = 0, None, True
        self.sad_resource_interrupt, self.sad_resource_type = 1, 2
        self.power_state, self.power_queries = 1, 0
        self.qos = object()
        self.node = NS(create_client=self.client, create_subscription=self.subscribe,
                       destroy_client=lambda item: self.destroyed.append(item.name),
                       destroy_subscription=lambda item: None,
                       get_clock=lambda: NS(now=lambda: NS(to_msg=lambda: None)))

    def client(self, kind, path):
        return FakeClient(self, kind.__name__)

    def subscribe(self, kind, path, callback, qos):
        self.callbacks.append((path, callback))
        return callback

    def spin(self, seconds=.05, cancellable=True):
        if cancellable:
            self.checkpoint()
        self.now += max(seconds, .02)
        if self.on_spin:
            self.on_spin(self)
        if self.writes and self.writes[-1][0] == 'motion':
            self.mimic_checks += 1
            if self.mimic_checks >= 2 and self.mimic_completes:
                self.action, self.moving = self.mimic_entry, False
        state = NS(is_moving=self.moving, is_fallen=self.fallen,
                   action_info=NS(current_action=NS(value=self.action),
                                  status=NS(value=self.action_status), switch_ready=self.switch_ready),
                   task_info=NS(task_id=self.task_id), form_state=NS(current_form=self.form),
                   lift_state=NS(is_lifted=self.lifted), held_state=NS(is_held=self.held))
        for path, callback in self.callbacks:
            if path == '/aima/hal/bms/state':
                if not self.battery_missing:
                    callback(NS(status=self.battery_status))
            else:
                callback(state)

    def checkpoint(self):
        if self.cancel:
            raise Cancelled('cancelled')

    def rpc(self, name, request):
        if name == 'GetJointPowerState':
            self.power_queries += 1
            if self.fail == name:
                raise AudioError('power query failed')
            return NS(response=NS(header=NS(code=0), status=NS(value=1, reason=0)),
                      power_state=self.power_state)
        if name == 'GetMcAction':
            return NS(header=NS(code=0), info=NS(current_action=NS(value=self.action),
                                                status=NS(value=self.action_status)))
        if name == 'GetMcMotions':
            resources = [NS(tag=str(value), type=NS(value=1), intr_info=NS(intr_type=NS(value=1)))
                         for value in (0, 2002, 3005, 2033)]
            resources.append(NS(tag='QUAD_MIMIC_SAD', type=NS(value=self.sad_resource_type, MIMIC=2),
                                intr_info=NS(intr_type=NS(value=self.sad_resource_interrupt))))
            return NS(response=NS(header=NS(code=0)), motion=resources)
        if name == 'GetMcPresetMotionState':
            self.preset_checks[self.task_id] = self.preset_checks.get(self.task_id, 0) + 1
            value = 400 if self.preset_checks[self.task_id] == 1 else 1
            if value == 1:
                self.moving = False
                if self.writes[-1] == ('preset', 0) and self.action == 1005:
                    self.action = 1002
            return NS(response=NS(header=NS(code=0), task_id=self.task_id,
                                  state=NS(value=value, reason=0)))
        if name == 'SetMcAction':
            self.writes.append(('action', request.command.action.value))
            self.action = request.command.action.value
            self.moving = self.action in (114, 121)
        elif name == 'SetMcPresetMotion':
            self.writes.append(('preset', request.motion.value))
            self.moving = True
        elif name == 'SetMcMotion':
            self.writes.append(('motion', request.motion))
            self.mimic_entry, self.action, self.moving = self.action, 119, True
            self.mimic_checks = 0
            if (request.source, request.type, request.operation, request.model,
                    request.resource, request.interrupt) != ('sdk_rpc', 1, 2, '', '', False):
                raise AssertionError('invalid mimic request')
        else:
            raise AssertionError(name)
        if self.fail == name:
            raise AudioError('uncertain transport')
        self.task_id += 1
        return NS(response=NS(header=NS(code=0), task_id=self.task_id, state=NS(value=400, reason=0)))

    def call(self, client, request, cancellable=True, before_submit=None):
        if cancellable:
            self.checkpoint()
        if before_submit is not None:
            before_submit()
        self.faces.append((request.emotion_ids, request.loop_count, cancellable))
        if self.fail == 'PlayEmotion':
            raise AudioError('uncertain emotion')


class CelebrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.robot = FakeRobot()
        self.identity = Device('T1', 'T1-2X', '0')
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, messages()).start()
        patch('chengyu_game.robot.celebration.time.monotonic', lambda: self.robot.now).start()
        patch('chengyu_game.robot.celebration.detect_device', lambda: self.identity).start()
        patch('chengyu_game.robot.celebration.load_quad_route',
              lambda runtime, initial: {'enter': [114], 'return': [initial]}).start()
        patch('chengyu_game.robot.celebration.play_expression', fake_expression).start()
        original = Path.read_text
        patch.object(Path, 'read_text', lambda path, *args, **kwargs: 'boot-test' if str(path) ==
                     '/proc/sys/kernel/random/boot_id' else original(path, *args, **kwargs)).start()

    def build(self, motion=False, **settings):
        config = {'motion_enabled': motion, 'commissioned': motion,
                  'commissioned_for': {'series': self.identity.series, 'sku': self.identity.sku},
                  'verified': {'T1_biped': motion, 'T1_quad': motion, 'Q1': motion},
                  'verified_stand': {'T1_quad': motion}, **settings}
        result = Celebration(self.robot, self.runtime, {'celebration': config})
        result.start()
        return result

    def test_default_expression_has_one_loop_then_reset_without_motion(self):
        effects = self.build()
        effects.celebrate('win')
        self.assertEqual(self.robot.faces, [([90], 1, True), ([13], 1, False)])
        self.assertEqual(self.robot.writes, [])
        self.assertFalse(effects.path.exists())
        effects.close()

    def test_q1_expression_id_is_not_happiness_motion_id(self):
        self.identity = Device('Q1', 'Q1-1X', '0')
        effects = self.build()
        effects.celebrate('win')
        self.assertEqual([item[0] for item in self.robot.faces], [[3003], [3001]])

    def test_each_commissioning_gate_must_be_explicit(self):
        cases = [{'commissioned': False}, {'motion_enabled': False}, {'verified': {}},
                 {'commissioned_for': {'series': 'T1', 'sku': 'another'}}]
        for values in cases:
            with self.subTest(values=values):
                effects = self.build(True, **values)
                effects.celebrate('win')
                effects.close()
        self.assertEqual(self.robot.writes, [])

    def test_t1_biped_completes_preset_and_restores_initial_mode(self):
        self.robot.action = 201
        effects = self.build(True)
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 300), ('preset', 2002),
                                             ('preset', 0), ('action', 201)])
        self.assertFalse(effects.path.exists())

    def test_q1_happiness_and_reset_are_preset_protocol(self):
        self.identity = Device('Q1', 'Q1-1X', '0')
        self.robot.action = 1002
        effects = self.build(True)
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 1005), ('preset', 3005), ('preset', 0)])
        self.assertEqual(self.robot.action, 1002)
        self.assertFalse(effects.path.exists())

    def test_quad_uses_short_action_then_original_quad_action(self):
        self.robot.action, self.robot.form = 102, 1
        effects = self.build(True)
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 114), ('action', 102)])
        self.assertFalse(effects.path.exists())

    def test_t1_complete_expression_does_not_extend_short_body_motion(self):
        self.robot = FakeRobot(102)
        effects = self.build(True)
        face_times, body_times = [], []
        original_face, original_rpc = self.robot.call, self.robot.rpc
        def face(*args, **kwargs):
            face_times.append(self.robot.now)
            return original_face(*args, **kwargs)
        def rpc(name, request):
            if name == 'SetMcAction':
                body_times.append(self.robot.now)
            return original_rpc(name, request)
        self.robot.call, self.robot.rpc = face, rpc
        effects.celebrate('win')
        self.assertGreaterEqual(face_times[1] - face_times[0], 5)
        self.assertLess(body_times[1] - body_times[0], 1.6)
        self.assertEqual(self.robot.writes, [('action', 114), ('action', 102)])
        self.assertFalse(effects.path.exists())

    def test_early_body_return_failure_is_not_retried_by_finally_or_close(self):
        self.robot = FakeRobot(102)
        effects = self.build(True)
        original = self.robot.rpc
        def reject_return(name, request):
            if name == 'SetMcAction' and request.command.action.value == 102:
                self.robot.writes.append(('action', 102))
                return NS(response=NS(header=NS(code=9)))
            return original(name, request)
        self.robot.rpc = reject_return
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        with self.assertRaises(AudioError):
            effects.close()
        self.assertEqual(self.robot.writes, [('action', 114), ('action', 102)])
        self.assertTrue(effects.path.exists())

    def test_current_firmware_uses_step_before_happy_and_preserves_return_route(self):
        for initial, returning in ((102, [102]), (101, [121, 101])):
            with self.subTest(initial=initial):
                self.robot = FakeRobot(initial)
                effects = self.build(True, verified_step={'T1_quad': True})
                route = {'enter': [121, 114], 'return': returning}
                with patch('chengyu_game.robot.celebration.load_quad_route',
                           return_value=route, create=True):
                    effects.celebrate('win')
                expected = [121, 114, *returning]
                self.assertEqual(self.robot.writes, [('action', item) for item in expected])
                self.assertFalse(effects.path.exists())

    def test_new_step_route_is_not_enabled_by_old_happy_commission(self):
        self.robot = FakeRobot(102)
        effects = self.build(True)
        with patch('chengyu_game.robot.celebration.load_quad_route',
                   return_value={'enter': [121, 114], 'return': [102]}, create=True):
            result = effects.celebrate('win')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])

    def step_effects(self):
        self.robot = FakeRobot(102)
        patch('chengyu_game.robot.celebration.load_quad_route',
              return_value={'enter': [121, 114], 'return': [102]}).start()
        return self.build(True, verified_step={'T1_quad': True})

    def test_cancel_after_step_returns_without_starting_happy(self):
        effects = self.step_effects()
        self.robot.on_spin = lambda robot: setattr(robot, 'cancel', bool(robot.writes))
        with self.assertRaises(Cancelled):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 102)])
        self.assertFalse(effects.path.exists())

    def test_step_waits_for_running_ready_feedback_before_happy(self):
        effects = self.step_effects()
        samples = []
        def transitioning(robot):
            if robot.action == 121:
                samples.append(robot.now)
                robot.action_status = 200 if len(samples) < 3 else 100
                robot.switch_ready = len(samples) >= 4
            else:
                robot.action_status, robot.switch_ready = 100, True
        self.robot.on_spin = transitioning
        effects.celebrate('win')
        self.assertGreaterEqual(len(samples), 4)
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 114), ('action', 102)])

    def test_unknown_step_status_is_not_treated_as_running(self):
        effects = self.step_effects()
        def unknown(robot):
            if robot.writes:
                robot.action_status = 300
        self.robot.on_spin = unknown
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 121)])
        self.assertTrue(effects.path.exists())

    def test_brief_unclassified_transition_waits_for_running_before_next_control(self):
        effects = self.step_effects()
        counts = {}
        def transition(robot):
            if robot.writes:
                counts[robot.task_id] = counts.get(robot.task_id, 0) + 1
                robot.action_status = 300 if counts[robot.task_id] < 3 else 100
        self.robot.on_spin = transition
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 114), ('action', 102)])
        self.assertFalse(effects.path.exists())

    def test_happy_naturally_ends_to_102_and_returns_to_original_101(self):
        self.robot = FakeRobot(101)
        effects = self.build(True, verified_step={'T1_quad': True})
        seen = []
        def finish(robot):
            if robot.action == 114:
                seen.append(robot.now)
                if len(seen) >= 3:
                    robot.action, robot.moving = 102, False
        self.robot.on_spin = finish
        with patch('chengyu_game.robot.celebration.load_quad_route',
                   return_value={'enter': [121, 114], 'return': [121, 101]}):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 114), ('action', 121), ('action', 101)])
        self.assertFalse(effects.path.exists())

    def test_recover_accepted_happy_already_at_natural_end_without_repeating_happy(self):
        self.robot = FakeRobot(102)
        route = {'enter': [121, 114], 'return': [121, 101]}
        effects = self.build()
        write_json(effects.path, {'version': 1, 'series': 'T1', 'sku': 'T1-2X',
            'motion': {'profile': 'T1_quad', 'initial_action': 101, 'action': 114, 'task_id': 7,
                       'owner_seen': False, 'stage': 'mode', 'boot_id': 'boot-test',
                       'mood': 'happy', 'quad_route': route}})
        with patch('chengyu_game.robot.celebration.load_quad_route', return_value=route):
            effects.recover()
        self.assertEqual(self.robot.writes, [('action', 121), ('action', 101)])
        self.assertFalse(effects.path.exists())

    def test_external_task_after_step_is_never_overwritten_by_return(self):
        effects = self.step_effects()
        def takeover(robot):
            if robot.writes:
                robot.task_id = 999
        self.robot.on_spin = takeover
        with self.assertRaisesRegex(AudioError, '其他任务'):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('action', 121)])
        self.assertTrue(effects.path.exists())

    def test_lifted_held_fallen_and_resting_robot_get_expression_only(self):
        for field in ('lifted', 'held', 'fallen'):
            with self.subTest(field=field):
                self.robot = FakeRobot()
                setattr(self.robot, field, True)
                effects = self.build(True)
                with self.assertLogs('chengyu_game.robot.celebration', level='INFO') as logs:
                    effects.celebrate('win')
                self.assertEqual(self.robot.writes, [])
                self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
                self.assertIn('motion_skipped', logs.output[0])
                self.assertFalse(effects.path.exists())
        self.robot = FakeRobot(action=1)
        effects = self.build(True)
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_wrong_form_never_causes_motion(self):
        self.robot.form = 1
        effects = self.build(True)
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_unknown_motion_response_is_persisted_and_never_retried(self):
        self.robot.fail = 'SetMcPresetMotion'
        effects = self.build(True)
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertEqual(read_json(effects.path)['motion']['pending'], 'SetMcPresetMotion')
        self.assertEqual(self.robot.writes, [('preset', 2002)])
        with self.assertRaises(AudioError):
            effects.close()
        recovered = Celebration(self.robot, self.runtime, {})
        with self.assertRaises(AudioError):
            recovered.recover()
        self.assertEqual(self.robot.writes, [('preset', 2002)])

    def test_expression_unknown_keeps_record_without_resend(self):
        effects = self.build()
        self.robot.fail = 'PlayEmotion'
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')
        with self.assertRaises(AudioError):
            effects.close()
        self.assertEqual(len(self.robot.faces), 1)

    def test_original_expression_error_survives_cleanup_and_close(self):
        effects = self.build()
        self.robot.fail = 'PlayEmotion'
        with self.assertRaisesRegex(AudioError, 'uncertain emotion'):
            effects.celebrate('win')
        with self.assertRaisesRegex(AudioError, 'uncertain emotion'):
            effects.close()
        self.assertEqual(len(self.robot.faces), 1)
        self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')

    def test_expression_unready_before_submit_keeps_no_pending_or_body_control(self):
        effects = self.build(True)
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        effects.clients['PlayEmotion'].service_is_ready = lambda: False
        with self.assertRaisesRegex(AudioError, '服务未就绪'):
            effects.celebrate('win')
        self.assertFalse(effects.path.exists())
        self.assertEqual(self.robot.writes, [])
        effects.close()

    def test_explicit_expression_rejection_clears_only_its_pending(self):
        effects = self.build(True)
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        effects.clients['PlayEmotion'].call_async = lambda request: NS(done=lambda: True, result=lambda:
            NS(header=NS(header=NS(code=7), status=NS(value=2, reason=17))))
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertFalse(effects.path.exists())
        self.assertEqual(self.robot.writes, [])
        effects.close()

    def test_expression_timeout_retains_pending_and_its_original_reason(self):
        effects = self.build(True)
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        calls = []
        def submit(request):
            self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')
            calls.append(request)
            return NS(done=lambda: False)
        effects.clients['PlayEmotion'].call_async = submit
        with self.assertRaisesRegex(AudioError, '请求超时'):
            effects.celebrate('win')
        with self.assertRaisesRegex(AudioError, '请求超时'):
            effects.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')

    def test_zero_code_unknown_expression_status_is_not_accepted_or_cleared(self):
        effects = self.build()
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        calls = []
        def submit(request):
            calls.append(request)
            return NS(done=lambda: True, result=lambda:
                      NS(header=NS(header=NS(code=0), status=NS(value=0, reason=0))))
        effects.clients['PlayEmotion'].call_async = submit
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertEqual(len(calls), 1)
        self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')

    def test_expression_dispatch_exception_remains_pending_without_resend(self):
        effects = self.build()
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        calls = []
        def submit(request):
            self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')
            calls.append(request)
            raise TypeError('dispatch result unknown')
        effects.clients['PlayEmotion'].call_async = submit
        with self.assertRaisesRegex(AudioError, 'dispatch result unknown'):
            effects.celebrate('win')
        with self.assertRaisesRegex(AudioError, 'dispatch result unknown'):
            effects.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual(read_json(effects.path)['expression']['pending'], 'happy')

    def test_expression_cancelled_before_submit_leaves_no_pending(self):
        effects = self.build()
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        self.robot.cancel = True
        with self.assertRaises(Cancelled):
            effects.celebrate('win')
        self.assertFalse(effects.path.exists())
        self.assertEqual(self.robot.faces, [])
        effects.close()

    def test_rejected_reset_preserves_accepted_expression_without_automatic_retry(self):
        effects = self.build()
        self.robot.call = lambda *args, **kwargs: Robot.call(self.robot, *args, **kwargs)
        calls = []
        def submit(request):
            calls.append(request.emotion_ids)
            code = 0 if len(calls) == 1 else 7
            return NS(done=lambda: True, result=lambda:
                      NS(header=NS(header=NS(code=code), status=NS(value=1 if code == 0 else 2, reason=0))))
        effects.clients['PlayEmotion'].call_async = submit
        with self.assertRaisesRegex(AudioError, '明确拒绝'):
            effects.celebrate('win')
        self.assertEqual(read_json(effects.path)['expression'], {'accepted': True})
        with self.assertRaisesRegex(AudioError, '明确拒绝'):
            effects.close()
        self.assertEqual(calls, [[90], [13]])

    def test_owned_recovery_does_not_depend_on_enabled_config(self):
        write_json(self.runtime / 'effects.json', {'version': 1, 'series': 'T1', 'sku': 'T1-2X',
                                                  'expression': {'accepted': True}})
        effects = Celebration(self.robot, self.runtime, {'celebration': {'enabled': False}})
        effects.recover()
        self.assertEqual(self.robot.faces, [([13], 1, False)])
        self.assertFalse(effects.path.exists())

    def test_identity_mismatch_does_not_write_even_on_close(self):
        write_json(self.runtime / 'effects.json', {'version': 1, 'series': 'Q1', 'sku': 'Q1-1X',
                                                  'expression': {'accepted': True}})
        effects = Celebration(self.robot, self.runtime, {})
        with self.assertRaises(AudioError):
            effects.recover()
        effects.close()
        self.assertEqual(self.robot.faces, [])
        self.assertTrue(effects.path.exists())

    def saved_motion(self, **fields):
        return {'version': 1, 'series': 'T1', 'sku': 'T1-2X',
                'motion': {'profile': 'T1_biped', 'initial_action': 300, 'task_id': 7,
                           'stage': 'preset', 'owner_seen': True, 'boot_id': 'boot-test', **fields}}

    def test_recovery_does_not_override_another_task(self):
        write_json(self.runtime / 'effects.json', self.saved_motion())
        self.robot.task_id = 88
        effects = Celebration(self.robot, self.runtime, {})
        with self.assertRaisesRegex(AudioError, '其他任务'):
            effects.recover()
        self.assertEqual(self.robot.writes, [])
        self.assertTrue(effects.path.exists())

    def test_reboot_does_not_reuse_old_task_id(self):
        write_json(self.runtime / 'effects.json', self.saved_motion(boot_id='previous-boot'))
        effects = Celebration(self.robot, self.runtime, {})
        with self.assertRaisesRegex(AudioError, '重启'):
            effects.recover()
        self.assertEqual(self.robot.writes, [])
        self.assertTrue(effects.path.exists())

    def test_confirmed_owned_preset_can_be_recovered(self):
        write_json(self.runtime / 'effects.json', self.saved_motion())
        effects = Celebration(self.robot, self.runtime, {})
        effects.recover()
        self.assertEqual(self.robot.writes, [('preset', 0)])
        self.assertFalse(effects.path.exists())

    def test_cancel_after_face_ack_cleans_face_without_replaying(self):
        effects = self.build()
        original = self.robot.call
        def cancel_after_ack(*args, **kwargs):
            original(*args, **kwargs)
            self.robot.cancel = True
        self.robot.call = cancel_after_ack
        with self.assertRaises(Cancelled):
            effects.celebrate('win')
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_safety_is_rechecked_after_waiting_for_service(self):
        effects = self.build(True)
        original_ready = FakeClient.service_is_ready
        waiting = [True]
        def ready(client):
            if client.name == 'SetMcPresetMotion' and waiting[0]:
                waiting[0] = False
                self.robot.lifted = True
                return False
            return original_ready(client)
        with patch.object(FakeClient, 'service_is_ready', ready):
            with self.assertRaises(AudioError):
                effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertFalse(effects.path.exists())

    def test_external_takeover_during_preset_does_not_send_reset(self):
        effects = self.build(True)
        def take_over(robot):
            if robot.writes:
                robot.task_id = 99
        self.robot.on_spin = take_over
        with self.assertRaisesRegex(AudioError, '其他任务'):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('preset', 2002)])
        self.assertTrue(effects.path.exists())

    def test_success_without_running_feedback_does_not_release_record(self):
        effects = self.build(True, max_motion_seconds=1)
        original = self.robot.rpc
        def never_running(name, request):
            result = original(name, request)
            if name == 'GetMcPresetMotionState':
                result.response.state.value = 1
                self.robot.moving = False
            return result
        self.robot.rpc = never_running
        with self.assertRaisesRegex(AudioError, '运行后完成'):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('preset', 2002), ('preset', 0)])
        self.assertTrue(effects.path.exists())
        with self.assertRaises(AudioError):
            effects.close()
        self.assertEqual(self.robot.writes, [('preset', 2002), ('preset', 0)])

    def test_unusable_recovery_record_is_retained_without_writes(self):
        saved = self.saved_motion(profile='Q1', initial_action=9999)
        write_json(self.runtime / 'effects.json', saved)
        effects = Celebration(self.robot, self.runtime, {})
        with self.assertRaisesRegex(AudioError, '记录不完整'):
            effects.recover()
        effects.close()
        self.assertEqual(self.robot.writes, [])
        self.assertTrue(effects.path.exists())

    def test_stale_state_degrades_to_expression_only(self):
        effects = self.build(True)
        original = effects._on_mc
        def stale(message):
            original(message)
            effects.received_at -= 3
        effects._on_mc = stale
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_missing_hold_feedback_degrades_to_expression_only(self):
        effects = self.build(True)
        original = effects._on_mc
        def missing(message):
            del message.held_state
            original(message)
        effects._on_mc = missing
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_final_checkpoint_blocks_call_async_without_unknown_pending(self):
        effects = self.build(True)
        original = self.robot.checkpoint
        def cancel_before_dispatch():
            if effects.record.get('motion', {}).get('pending'):
                raise Cancelled('cancelled before dispatch')
            original()
        self.robot.checkpoint = cancel_before_dispatch
        with self.assertRaisesRegex(Cancelled, 'before dispatch'):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13]])
        self.assertFalse(effects.path.exists())

    def test_pending_is_on_disk_before_motion_rpc(self):
        effects = self.build(True)
        original = self.robot.rpc
        def inspect_pending(name, request):
            if name.startswith('SetMc'):
                self.assertEqual(read_json(effects.path)['motion']['pending'], name)
            return original(name, request)
        self.robot.rpc = inspect_pending
        effects.celebrate('win')
        self.assertFalse(effects.path.exists())

    def test_t1_loss_uses_sad_face_and_resets_without_unverified_sad_motion(self):
        effects = self.build(True)
        result = effects.celebrate('lose')
        self.assertEqual([item[0] for item in self.robot.faces], [[21], [13]])
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(result['expression']['status'], 'accepted_and_reset')
        self.assertFalse(effects.path.exists())

    def test_q1_loss_uses_verified_sad_face_without_unavailable_body_motion(self):
        self.identity = Device('Q1', 'Q1-1X', '0')
        self.robot.action = 1002
        effects = self.build(True)
        result = effects.sad()
        self.assertEqual([item[0] for item in self.robot.faces], [[3018], [3001]])
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(result['expression']['status'], 'accepted_and_reset')
        self.assertFalse(effects.path.exists())

    def test_answers_only_use_faces_even_with_all_motion_permissions(self):
        for identity, action, happy, sad, reset in (
                (Device('T1', 'T1-2X', '0'), 300, 90, 21, 13),
                (Device('T1', 'T1-2X', '0'), 101, 90, 21, 13),
                (Device('Q1', 'Q1_Explorer_Edition', '0'), 1002, 3003, 3018, 3001)):
            with self.subTest(series=identity.series, action=action):
                self.identity, self.robot = identity, FakeRobot(action)
                effects = self.build(True, verified_sad={'T1_biped': True, 'T1_quad': True, 'Q1': True})
                for outcome, face in (('success', happy), ('failure', sad)):
                    result = effects.celebrate(outcome)
                    self.assertEqual(result['motion']['status'], 'not_requested')
                    self.assertEqual(result['expression']['status'], 'accepted_and_reset')
                    self.assertEqual(self.robot.faces[-2:], [([face], 1, True), ([reset], 1, False)])
                self.assertEqual(self.robot.writes, [])
                self.assertEqual(self.robot.power_queries, 0)
                self.assertFalse(effects.path.exists())
                effects.close()

    def test_win_and_failure_are_explicit_outcomes(self):
        effects = self.build()
        self.assertEqual(effects.celebrate('win')['outcome'], 'win')
        self.assertEqual(effects.celebrate('failure')['outcome'], 'failure')
        with self.assertRaisesRegex(AudioError, '反馈类型'):
            effects.celebrate('unknown')
        self.assertEqual([item[0] for item in self.robot.faces], [[90], [13], [21], [13]])

    def test_each_charging_or_connected_bit_blocks_motion(self):
        for status in (1, 1 << 25, 1 << 26):
            with self.subTest(status=status):
                self.robot = FakeRobot()
                self.robot.battery_status = status
                effects = self.build(True)
                result = effects.celebrate('win')
                self.assertEqual(result['motion']['status'], 'unavailable')
                self.assertIn('充电', result['motion']['reason'])
                self.assertEqual(self.robot.writes, [])

    def test_unknown_or_stale_battery_never_starts_motion(self):
        for status in (None, -1, 1 << 32, True, '0'):
            with self.subTest(status=status):
                self.robot = FakeRobot()
                self.robot.battery_status = status
                effects = self.build(True)
                result = effects.celebrate('win')
                self.assertEqual(result['motion']['status'], 'unavailable')
                self.assertEqual(self.robot.writes, [])
        self.robot = FakeRobot()
        effects = self.build(True)
        original = effects._on_bms
        def stale(message):
            original(message)
            effects.battery_received_at -= 3
        effects._on_bms = stale
        result = effects.celebrate('win')
        self.assertIn('反馈过期', result['motion']['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_charger_connection_while_waiting_blocks_dispatch(self):
        effects = self.build(True)
        original = FakeClient.service_is_ready
        def ready(client):
            if client.name == 'SetMcPresetMotion':
                self.robot.battery_status = 1 << 25
            return original(client)
        with patch.object(FakeClient, 'service_is_ready', ready):
            with self.assertRaisesRegex(AudioError, '充电'):
                effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])
        self.assertFalse(effects.path.exists())

    def test_motion_on_does_not_bypass_commissioning(self):
        effects = self.build()
        result = effects.prepare_for_game(motion_enabled=True)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('现场验收', result['reason'])
        self.assertEqual(self.robot.writes, [])
        self.assertEqual(self.robot.faces, [])

    def test_motion_off_override_prevents_preparation_and_celebration_motion(self):
        effects = self.build(True)
        self.assertEqual(effects.prepare_for_game(motion_enabled=False)['status'], 'disabled')
        effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])

    def test_verified_t1_seated_and_prone_prepare_one_stand_without_form_change(self):
        for action in (110, 111):
            with self.subTest(action=action):
                self.robot = FakeRobot(action)
                self.robot.form = 1
                effects = self.build(True)
                notices = []
                def notice():
                    self.assertEqual(self.robot.writes, [])
                    self.assertEqual(self.robot.action, action)
                    notices.append('played_to_end')
                result = effects.prepare_for_game(before_stand=notice)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(notices, ['played_to_end'])
                self.assertEqual(self.robot.writes, [('action', 101)])
                self.assertEqual(self.robot.action, 101)
                self.assertEqual(self.robot.form, 1)
                self.assertFalse(effects.path.exists())
                self.assertEqual(self.robot.faces, [])

    def test_stand_notice_failure_or_cancellation_never_moves(self):
        for error in (AudioError('playback unknown'), Cancelled('cancelled')):
            with self.subTest(error=type(error).__name__):
                self.robot = FakeRobot(110)
                self.robot.form = 1
                effects = self.build(True)
                def notice():
                    raise error
                with self.assertRaises((SetupError, Cancelled)):
                    effects.prepare_for_game(before_stand=notice)
                effects.close()
                self.assertEqual(self.robot.writes, [])
                self.assertFalse(effects.path.exists())

    def test_standing_or_disabled_motion_does_not_announce_or_stand(self):
        for action, enabled in ((101, True), (102, True), (110, False)):
            with self.subTest(action=action, enabled=enabled):
                self.robot = FakeRobot(action)
                self.robot.form = 1
                effects = self.build(True)
                def unexpected_notice():
                    self.fail('no stand needed')
                effects.prepare_for_game(motion_enabled=enabled, before_stand=unexpected_notice)
                self.assertEqual(self.robot.writes, [])

    def test_external_takeover_during_stand_notice_is_not_overwritten(self):
        self.robot.action, self.robot.form = 110, 1
        effects = self.build(True)
        def notice():
            self.robot.task_id += 1
            self.robot.spin()
        result = effects.prepare_for_game(before_stand=notice)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('接管', result['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_celebration_does_not_silently_stand_outside_game_start(self):
        self.robot.action, self.robot.form = 110, 1
        result = self.build(True).celebrate('win')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])

    def test_native_stand_intermediate_modes_are_observed_without_extra_commands(self):
        self.robot.action, self.robot.form = 110, 1
        effects = self.build(True)
        stages = iter([(115, 200), (100, 300), (121, 300), (101, 100)])
        observed = []
        def transition(robot):
            if robot.writes:
                robot.action, robot.action_status = next(stages, (101, 100))
                observed.append((robot.action, robot.action_status))
                robot.moving = robot.action != 101
        self.robot.on_spin = transition
        result = effects.prepare_for_game(before_stand=lambda: None)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.robot.writes, [('action', 101)])
        self.assertIn((100, 300), observed)
        self.assertFalse(effects.path.exists())

    def test_unknown_stand_status_never_counts_as_standing_or_reissues_request(self):
        self.robot.action, self.robot.form = 110, 1
        effects = self.build(True)
        def transition(robot):
            if robot.writes:
                robot.action_status = 300
        self.robot.on_spin = transition
        with self.assertRaises(AudioError):
            effects.prepare_for_game(before_stand=lambda: None)
        self.assertEqual(self.robot.writes, [('action', 101)])
        self.assertTrue(effects.path.exists())

    def test_passive_or_q1_seated_is_not_assumed_to_have_verified_stand_route(self):
        for series, action in (('T1', 1), ('Q1', 1), ('Q1', 1013)):
            with self.subTest(series=series, action=action):
                self.identity = Device(series, series + '-1X', '0')
                self.robot = FakeRobot(action)
                self.robot.form = 1
                effects = self.build(True)
                result = effects.prepare_for_game()
                self.assertEqual(result['status'], 'unavailable')
                self.assertIn('未自动起身', result['reason'])
                self.assertEqual(self.robot.writes, [])

    def test_unknown_stand_request_is_recorded_without_retry_or_sit_restore(self):
        self.robot.action, self.robot.form = 111, 1
        self.robot.fail = 'SetMcAction'
        effects = self.build(True)
        with self.assertRaisesRegex(AudioError, 'uncertain transport'):
            effects.prepare_for_game(before_stand=lambda: None)
        self.assertEqual(self.robot.writes, [('action', 101)])
        self.assertEqual(read_json(effects.path)['motion']['pending'], 'SetMcAction')
        with self.assertRaisesRegex(AudioError, 'uncertain transport'):
            effects.close()
        self.assertEqual(self.robot.writes, [('action', 101)])

    def test_stand_requires_matching_deployed_enums(self):
        self.robot.action, self.robot.form = 111, 1
        sys.modules['aimdk_msgs.msg'].McAction.QUADRUPED_STAND_DEFAULT = 999
        effects = self.build(True)
        result = effects.prepare_for_game()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('枚举', result['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_happiness_commission_does_not_imply_stand_preparation_commission(self):
        self.robot.action, self.robot.form = 111, 1
        effects = self.build(True, verified_stand={})
        result = effects.prepare_for_game()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('独立现场验收', result['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_verified_t1_biped_sad_uses_runtime_symbol_and_restores(self):
        self.robot.action = 201
        effects = self.build(True, verified_sad={'T1_biped': True})
        result = effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('action', 300), ('preset', 2033),
                                            ('preset', 0), ('action', 201)])
        self.assertEqual(result['motion']['status'], 'completed')
        self.assertEqual([item[0] for item in self.robot.faces], [[21], [13]])
        self.assertFalse(effects.path.exists())

    def test_biped_sad_missing_or_mismatched_symbol_never_sends_numeric_fallback(self):
        for value in (None, 999):
            with self.subTest(value=value):
                self.robot = FakeRobot()
                sys.modules['aimdk_msgs.msg'].McPresetMotion.HUMANOID_SAD = value
                effects = self.build(True, verified_sad={'T1_biped': True})
                result = effects.celebrate('lose')
                self.assertEqual(result['motion']['status'], 'unavailable')
                self.assertEqual(self.robot.writes, [])

    def test_quad_sad_waits_for_owned_mimic_and_natural_stationary_return(self):
        self.robot.action, self.robot.form = 102, 1
        effects = self.build(True, verified_sad={'T1_quad': True})
        result = effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD')])
        self.assertEqual(result['motion']['status'], 'completed')
        self.assertEqual(self.robot.action, 102)
        self.assertFalse(effects.path.exists())

    def test_quad_sad_cancel_stops_owned_interruptible_motion_and_restores_face(self):
        self.robot.action, self.robot.form, self.robot.mimic_completes = 102, 1, False
        effects = self.build(True, verified_sad={'T1_quad': True})
        def cancel_running(robot):
            if robot.writes:
                robot.cancel = True
        self.robot.on_spin = cancel_running
        with self.assertRaises(Cancelled):
            effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD'), ('action', 102)])
        self.assertEqual([item[0] for item in self.robot.faces], [[21], [13]])
        self.assertFalse(effects.path.exists())

    def test_quad_sad_timeout_confirms_owned_stop_without_replaying_mimic(self):
        self.robot.action, self.robot.form, self.robot.mimic_completes = 102, 1, False
        effects = self.build(True, verified_sad={'T1_quad': True}, max_motion_seconds=1)
        with self.assertRaisesRegex(AudioError, '四足难过动作运行后完成'):
            effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD'), ('action', 102)])
        self.assertFalse(effects.path.exists())

    def test_quad_sad_external_task_takeover_retains_record_without_stop(self):
        self.robot.action, self.robot.form = 102, 1
        effects = self.build(True, verified_sad={'T1_quad': True})
        def takeover(robot):
            if robot.writes:
                robot.task_id = 99
        self.robot.on_spin = takeover
        with self.assertRaisesRegex(AudioError, '其他任务'):
            effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD')])
        self.assertTrue(effects.path.exists())

    def test_quad_sad_uninterruptible_resource_is_unavailable(self):
        self.robot.action, self.robot.form, self.robot.sad_resource_interrupt = 102, 1, 2
        effects = self.build(True, verified_sad={'T1_quad': True})
        result = effects.celebrate('lose')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertIn('自由打断', result['motion']['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_quad_sad_missing_request_operation_refuses_instead_of_guessing(self):
        self.robot.action, self.robot.form = 102, 1
        def incompatible_request():
            value = request()
            del value.operation
            return value
        sys.modules['aimdk_msgs.srv'].SetMcMotion.Request = staticmethod(incompatible_request)
        effects = self.build(True, verified_sad={'T1_quad': True})
        result = effects.celebrate('lose')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])

    def test_sad_mimic_unknown_dispatch_is_not_retried_or_overridden(self):
        self.robot.action, self.robot.form, self.robot.fail = 102, 1, 'SetMcMotion'
        effects = self.build(True, verified_sad={'T1_quad': True})
        with self.assertRaises(AudioError):
            effects.celebrate('lose')
        self.assertEqual(read_json(effects.path)['motion']['pending'], 'SetMcMotion')
        with self.assertRaises(AudioError):
            effects.close()
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD')])

    def test_q1_sad_flag_cannot_enable_an_unsupported_action(self):
        self.identity = Device('Q1', 'Q1-1X', '0')
        self.robot.action = 1002
        effects = self.build(True, verified_sad={'Q1': True})
        result = effects.celebrate('lose')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])
        self.assertEqual([item[0] for item in self.robot.faces], [[3018], [3001]])

    def test_biped_sad_cancel_confirms_default_pose_and_clears_own_record(self):
        effects = self.build(True, verified_sad={'T1_biped': True})
        def cancel_running(robot):
            if ('preset', 2033) in robot.writes:
                robot.cancel = True
        self.robot.on_spin = cancel_running
        with self.assertRaises(Cancelled):
            effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('preset', 2033), ('preset', 0)])
        self.assertEqual([item[0] for item in self.robot.faces], [[21], [13]])
        self.assertFalse(effects.path.exists())

    def test_owned_sad_mimic_recovery_stops_without_replaying(self):
        self.robot.action, self.robot.form, self.robot.moving = 119, 1, True
        saved = self.saved_motion(profile='T1_quad', initial_action=102, stage='mimic', mood='sad')
        write_json(self.runtime / 'effects.json', saved)
        effects = Celebration(self.robot, self.runtime, {})
        effects.recover()
        self.assertEqual(self.robot.writes, [('action', 102)])
        self.assertEqual(self.robot.faces, [])
        self.assertFalse(effects.path.exists())

    def test_sad_mimic_running_feedback_is_required_before_success(self):
        self.robot.action, self.robot.form = 102, 1
        effects = self.build(True, verified_sad={'T1_quad': True}, max_motion_seconds=1)
        def no_running(robot):
            if robot.writes:
                robot.action, robot.moving = 102, False
        self.robot.on_spin = no_running
        with self.assertRaisesRegex(AudioError, '运行后完成'):
            effects.celebrate('lose')
        self.assertEqual(self.robot.writes, [('motion', 'QUAD_MIMIC_SAD')])
        self.assertFalse(effects.path.exists())

    def test_single_standing_frame_cannot_confirm_continuous_stability(self):
        self.robot.action, self.robot.form = 111, 1
        effects = self.build(True)
        original = effects._on_mc
        standing_seen = []
        def freeze_after_first_standing_frame(message):
            if message.action_info.current_action.value == 101:
                if standing_seen:
                    return
                standing_seen.append(True)
            original(message)
        effects._on_mc = freeze_after_first_standing_frame
        with self.assertRaises(AudioError):
            effects.prepare_for_game(before_stand=lambda: None)
        self.assertEqual(self.robot.writes, [('action', 101)])
        self.assertTrue(effects.path.exists())

    def test_quad_sad_wrong_resource_type_never_dispatches_mimic(self):
        self.robot.action, self.robot.form, self.robot.sad_resource_type = 102, 1, 999
        effects = self.build(True, verified_sad={'T1_quad': True})
        result = effects.celebrate('lose')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])

    def test_quad_sad_missing_registered_type_symbol_never_guesses_from_request_type(self):
        self.robot.action, self.robot.form = 102, 1
        original = self.robot.rpc
        def no_type_symbol(name, request):
            result = original(name, request)
            if name == 'GetMcMotions':
                for item in result.motion:
                    if item.tag == 'QUAD_MIMIC_SAD':
                        del item.type.MIMIC
            return result
        self.robot.rpc = no_type_symbol
        effects = self.build(True, verified_sad={'T1_quad': True})
        result = effects.celebrate('lose')
        self.assertEqual(result['motion']['status'], 'unavailable')
        self.assertIn('注册资源类型', result['motion']['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_joint_power_off_or_unknown_blocks_motion(self):
        for value in (2, 0, None, 999):
            with self.subTest(value=value):
                self.robot = FakeRobot()
                self.robot.power_state = value
                effects = self.build(True)
                result = effects.celebrate('win')
                self.assertEqual(result['motion']['status'], 'unavailable')
                self.assertEqual(self.robot.writes, [])

    def test_joint_power_is_queried_again_immediately_before_dispatch(self):
        effects = self.build(True)
        original = FakeClient.service_is_ready
        def off_before_dispatch(client):
            if client.name == 'SetMcPresetMotion':
                self.robot.power_state = 2
            return original(client)
        with patch.object(FakeClient, 'service_is_ready', off_before_dispatch):
            with self.assertRaises(AudioError):
                effects.celebrate('win')
        self.assertEqual(self.robot.writes, [])

    def test_power_loss_during_motion_prevents_cleanup_pose_writes(self):
        effects = self.build(True)
        def power_off_running(robot):
            if robot.writes:
                robot.power_state = 2
        self.robot.on_spin = power_off_running
        with self.assertRaises(AudioError):
            effects.celebrate('win')
        self.assertEqual(self.robot.writes, [('preset', 2002)])
        self.assertTrue(effects.path.exists())

    def test_power_off_blocks_seated_stand_preparation(self):
        self.robot.action, self.robot.form, self.robot.power_state = 111, 1, 2
        effects = self.build(True)
        result = effects.prepare_for_game()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])
        self.assertFalse(effects.path.exists())

    def test_joint_power_query_failure_or_missing_enum_blocks_motion(self):
        effects = self.build(True)
        self.robot.fail = 'GetJointPowerState'
        self.assertEqual(effects.celebrate('win')['motion']['status'], 'unavailable')
        self.assertEqual(self.robot.writes, [])
        self.robot.fail = None
        sys.modules['aimdk_msgs.srv'].GetJointPowerState.Response.POWER_ON = None
        result = effects.celebrate('win')
        self.assertIn('枚举', result['motion']['reason'])
        self.assertEqual(self.robot.writes, [])

    def test_joint_power_unsuccessful_or_incomplete_response_blocks_motion(self):
        for issue in ('code', 'status', 'reason', 'schema'):
            with self.subTest(issue=issue):
                self.robot = FakeRobot()
                original = self.robot.rpc
                def bad_response(name, request):
                    result = original(name, request)
                    if name == 'GetJointPowerState':
                        if issue == 'code':
                            result.response.header.code = 3
                        elif issue == 'status':
                            result.response.status.value = 0
                        elif issue == 'reason':
                            result.response.status.reason = 19
                        else:
                            del result.response.status
                    return result
                self.robot.rpc = bad_response
                effects = self.build(True)
                result = effects.celebrate('win')
                self.assertEqual(result['motion']['status'], 'unavailable')
                self.assertEqual(self.robot.writes, [])

    def test_joint_power_on_cache_expires_within_half_second(self):
        effects = self.build(True)
        effects._initialize(motion=True)
        self.robot.spin()
        effects._safe(stationary=True)
        self.assertEqual(self.robot.power_queries, 1)
        self.robot.spin(.2)
        effects._safe(stationary=True)
        self.assertEqual(self.robot.power_queries, 1)
        self.robot.power_state = 2
        self.robot.spin(.31)
        with self.assertRaisesRegex(AudioError, '供电未明确为 ON'):
            effects._safe(stationary=True)
        self.assertEqual(self.robot.power_queries, 2)

    def test_failed_fresh_power_query_invalidates_previous_on_cache(self):
        effects = self.build(True)
        effects._initialize(motion=True)
        self.robot.spin()
        effects._safe(stationary=True)
        self.robot.fail = 'GetJointPowerState'
        with self.assertRaises(AudioError):
            effects._safe(stationary=True, fresh_power=True)
        self.robot.fail, self.robot.power_state = None, 2
        with self.assertRaisesRegex(AudioError, '供电未明确为 ON'):
            effects._safe(stationary=True)
        self.assertEqual(self.robot.power_queries, 3)


if __name__ == '__main__':
    unittest.main()
