"""Q1 原厂预设舞蹈的顺序播放与音乐结束恢复。"""
from pathlib import Path
import copy
import sys
import tempfile
from types import SimpleNamespace as Item
import unittest
from unittest.mock import patch

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]
effects = load_entry(ROOT, 'src/effects/music_effects.py')


class Request:
    MIMIC_QY = 1
    EXECUTION = 2

    def __init__(self):
        self.header = Item(stamp=None, header=Item(stamp=None))
        self.request = Item(header=Item(stamp=None), task_id=0)
        self.command = Item(action=Item(value=0), action_desc='')
        self.motion = Item(value=0)
        self.area = Item(value=0)
        self.form_state = Item(UNKNOWN=0, current_form=0)


def service(name):
    return type(name, (), {'Request': Request})


class Robot:
    def __init__(self):
        self.now = 10.
        self.action = 1002
        self.playing = False
        self.moving = False
        self.writes = []
        self.tasks = {}
        self.next_task_id = 0
        self.mc_task_id = 0
        self.global_query = False
        self.reset_state = 1
        self.preset_states = None
        self.reset_states = None
        self.default_auto_return = False
        self.dance_auto_return = False
        self.delayed_task_spins = 0
        self.pending_mc_task = None
        self.motions = [Item(tag=str(value), type=Item(value=1),
                             intr_info=Item(intr_type=Item(value=1))) for value in (0, 3046, 3047, 3048)]
        self.rgb = (255, 0, 0, 0, 0)
        self.subscriptions = {}
        self.after_action_read = lambda: None
        self.last_task_query = None
        self.qos = None
        self.node = self

    def get_clock(self):
        return Item(now=lambda: Item(to_msg=lambda: Item(sec=int(self.now), nanosec=0)))

    def create_client(self, kind, endpoint):
        name = endpoint.rsplit('/', 1)[-1]
        def call(request):
            response = Item(header=Item(code=0), task_id=0, state=Item(value=1, reason=0))
            if name == 'GetMcAction':
                reply = Item(header=Item(code=0), info=Item(
                    current_action=Item(value=self.action), status=Item(value=100), action_desc=''))
                self.after_action_read()
            elif name == 'GetMcMotions':
                reply = Item(response=response, motion=self.motions)
            elif name == 'GetMcPresetMotionState':
                task = request.request.task_id
                response.task_id = task
                response.state.value = self.tasks[task].pop(0) if len(self.tasks[task]) > 1 else self.tasks[task][0]
                self.last_task_query = (task, response.state.value)
                if response.state.value == 1 and self.dance_auto_return:
                    self.action = 1002
                if self.global_query:
                    response.task_id = 0
                reply = Item(response=response)
            elif name == 'LedStripCommand':
                self.rgb = tuple(getattr(request, key) for key in ('led_strip_mode', 'r', 'g', 'b', 'period'))
                reply = Item(header=Item(header=Item(code=0), status=Item(value=1)))
            else:
                self.writes.append((name, request))
                if name == 'SetMcAction':
                    self.action = request.command.action.value
                    self.next_task_id += 1
                    response.task_id = self.next_task_id
                    self.mc_task_id = response.task_id
                elif name == 'SetMcPresetMotion':
                    self.next_task_id += 1
                    response.task_id = self.next_task_id
                    if self.delayed_task_spins:
                        self.pending_mc_task = response.task_id
                    else:
                        self.mc_task_id = response.task_id
                    response.state.value = 400
                    self.tasks[response.task_id] = [400, 400, self.reset_state if request.motion.value == 0 else 1]
                    if self.preset_states is not None and request.motion.value != 0:
                        self.tasks[response.task_id] = list(self.preset_states)
                    if request.motion.value == 0:
                        if self.reset_states is not None:
                            self.tasks[response.task_id] = list(self.reset_states)
                        if self.default_auto_return:
                            self.action = 1002
                reply = Item(response=response)
            return Item(done=lambda: True, result=lambda: reply)
        return Item(service_is_ready=lambda: True, wait_for_service=lambda **_: True, call_async=call)

    def create_subscription(self, kind, topic, callback, qos):
        self.subscriptions[topic] = callback
        return topic

    def spin(self, seconds=.05, cancellable=True):
        self.now += max(seconds, .01)
        if self.pending_mc_task is not None:
            self.delayed_task_spins -= 1
            if self.delayed_task_spins <= 0:
                self.mc_task_id = self.pending_mc_task
                self.pending_mc_task = None
        for topic, callback in self.subscriptions.items():
            if topic == '/aima/mc/common/state':
                callback(Item(is_moving=self.moving, is_fallen=False,
                              action_info=Item(current_action=Item(value=self.action)),
                              task_info=Item(task_id=self.mc_task_id)))
            elif topic == '/aima/hal/rgb/state':
                callback(Item(**dict(zip(('display_mode', 'r', 'g', 'b', 'period'), self.rgb))))

    def call(self, *args, **kwargs):
        pass

    def destroy_subscription(self, subscription):
        pass

    def destroy_client(self, client):
        pass


class Q1MusicEffectsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.robot = Robot()
        names = ('GetMcAction', 'GetMcMotions', 'SetMcAction', 'SetMcMotion', 'LedStripCommand',
                 'PlayEmotion', 'SetMcPresetMotion', 'GetMcPresetMotionState')
        sdk = Item(**{name: service(name) for name in names})
        modules = {'aimdk_msgs': Item(), 'aimdk_msgs.srv': sdk,
                   'aimdk_msgs.msg': Item(McCommonState=object, RGBState=object)}
        patcher = patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(effects.time, 'monotonic', side_effect=lambda: self.robot.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def controller(self):
        controller = effects.Q1MusicEffects(self.robot, Path(self.directory.name) / 'effects.json',
                                            music_playing=lambda: self.robot.playing)
        controller.inspect()
        controller.prepare()
        return controller

    def test_q1_uses_small_preset_dance_and_queries_its_task_before_next_segment(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        motions = [request.motion.value for name, request in self.robot.writes if name == 'SetMcPresetMotion']
        self.assertEqual(len(motions), 1)
        self.assertIn(motions[0], (3046, 3047, 3048))
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])
        self.robot.now += 1
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)
        self.robot.now += 1
        controller.tick()
        motions = [request.motion.value for name, request in self.robot.writes if name == 'SetMcPresetMotion']
        self.assertEqual(len(motions), 2)
        self.assertNotEqual(motions[0], motions[1])
        self.assertFalse(any(name == 'SetMcMotion' for name, _ in self.robot.writes))

    def test_music_end_stops_loop_restores_default_pose_then_stable_walk_mode(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        self.robot.playing = False
        self.robot.now += 1
        controller.tick()
        controller.stop_music()
        motions = [request for name, request in self.robot.writes if name == 'SetMcPresetMotion']
        self.assertEqual(len(motions), 2)
        self.assertEqual(motions[-1].motion.value, 0)
        self.assertTrue(motions[-1].interrupt)
        self.assertEqual(self.robot.action, 1002)
        self.assertFalse(controller.record.exists())

    def test_music_end_while_waiting_for_service_cannot_start_another_segment(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        self.robot.tasks[controller.task_id] = [1]
        waiting = False
        def ready():
            nonlocal waiting
            result = waiting
            waiting = True
            return result
        client = controller.clients['SetMcPresetMotion']
        client.service_is_ready = ready
        spin = self.robot.spin
        def end_during_wait(*args, **kwargs):
            if waiting:
                self.robot.playing = False
            spin(*args, **kwargs)
        self.robot.spin = end_during_wait
        self.robot.now += 1
        controller.tick()
        self.assertFalse(self.robot.playing)
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)

    def test_unknown_preset_request_preserves_record_without_repeated_cleanup_commands(self):
        controller = self.controller()
        client = controller.clients['SetMcPresetMotion']
        call = client.call_async
        def timeout(request):
            call(request)
            return Item(done=lambda: False)
        client.call_async = timeout
        self.robot.playing = True
        with self.assertRaisesRegex(effects.AudioError, '超时'):
            controller.begin()
        self.robot.playing = False
        before = len(self.robot.writes)
        for _ in range(2):
            with self.assertRaises(effects.AudioError):
                controller.stop_music()
        self.assertEqual(len(self.robot.writes), before)
        self.assertTrue(controller.record.exists())

    def test_external_action_or_aborted_task_is_not_overridden(self):
        for change in ('action', 'task'):
            with self.subTest(change=change):
                controller = self.controller()
                self.robot.playing = True
                controller.begin()
                self.robot.playing = False
                if change == 'action':
                    self.robot.action = 1003
                else:
                    self.robot.tasks[controller.task_id] = [3]
                self.robot.spin()
                before = len(self.robot.writes)
                with self.assertRaises(effects.AudioError):
                    controller.stop_music()
                self.assertEqual(len(self.robot.writes), before)
                self.assertTrue(controller.record.exists())
                controller.record.unlink()
                self.robot.action = 1002

    def test_default_without_music_callback_never_sends_a_motion(self):
        controller = effects.Q1MusicEffects(self.robot, Path(self.directory.name) / 'effects.json')
        controller.inspect()
        controller.prepare()
        controller.begin()
        controller.stop_music()
        self.assertEqual(self.robot.writes, [])

    def test_default_pose_failure_keeps_record_and_never_forces_walk_mode(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        self.robot.reset_state = 3
        self.robot.playing = False
        with self.assertRaises(effects.AudioError):
            controller.stop_music()
        self.assertTrue(controller.record.exists())
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_missing_or_not_interruptible_preset_resource_prevents_any_motion(self):
        original = self.robot.motions
        for kind in ('missing', 'interrupt', 'type'):
            with self.subTest(kind=kind):
                self.robot.motions = copy.deepcopy(original)
                if kind == 'missing':
                    self.robot.motions = self.robot.motions[:-1]
                elif kind == 'interrupt':
                    self.robot.motions[0].intr_info.intr_type.value = 2
                else:
                    self.robot.motions[0].intr_info.intr_type.value = 1
                    self.robot.motions[0].type.value = 2
                with self.assertRaisesRegex(effects.AudioError, '预设资源'):
                    self.controller()
                self.assertEqual(self.robot.writes, [])

    def test_completed_task_waits_for_stationary_feedback_before_next_segment(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        self.robot.tasks[controller.task_id] = [1]
        self.robot.moving = True
        self.robot.now += 1
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)
        self.robot.moving = False
        self.robot.now += .2
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 2)

    def test_default_pose_can_return_to_walk_after_first_readback(self):
        controller = self.controller()
        self.robot.playing = True
        controller.begin()
        self.robot.playing = False
        def auto_return():
            if controller.last_motion == 0 and self.robot.last_task_query == (controller.task_id, 1):
                self.robot.action = 1002
        self.robot.after_action_read = auto_return
        controller.stop_music()
        self.assertFalse(controller.record.exists())
        self.assertEqual(self.robot.action, 1002)
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_global_success_without_running_is_logged_but_cannot_complete_a_segment(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.preset_states = [1]
        self.robot.playing = True
        with self.assertLogs(effects.logger, level='INFO') as captured:
            with self.assertRaisesRegex(effects.AudioError, '未证实本任务生命周期'):
                controller.begin()
        diagnostic = next(line for line in captured.output if 'Q1_PRESET_STATUS' in line)
        self.assertIn('"response_task_id": 0', diagnostic)
        self.assertIn('"mc_task_id": ' + str(controller.task_id), diagnostic)
        with patch.object(effects.logger, 'info') as log:
            with self.assertRaisesRegex(effects.AudioError, '未证实本任务生命周期'):
                controller.task_state()
            log.assert_not_called()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)
        self.assertTrue(controller.record.exists())

    def test_owned_global_running_then_success_allows_next_segment(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.playing = True
        controller.begin()
        self.robot.now += 1
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)
        self.robot.now += 1
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 2)

    def test_loading_old_mc_task_can_bind_but_later_task_change_is_not_overridden(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.delayed_task_spins = 4
        self.robot.preset_states = [400] * 10 + [1]
        self.robot.playing = True
        controller.begin()
        self.assertTrue(controller.task_owner_seen)
        self.assertTrue(controller.task_running_seen)
        before = len(self.robot.writes)
        self.robot.mc_task_id = 999999
        with self.assertRaisesRegex(effects.AudioError, '其他任务接管'):
            controller.task_state()
        with self.assertRaises(effects.AudioError):
            controller.stop_music()
        self.assertEqual(len(self.robot.writes), before)
        self.assertTrue(controller.record.exists())

    def test_cleanup_cannot_override_an_unbound_task(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.delayed_task_spins = 10000
        self.robot.playing = True
        with self.assertRaisesRegex(effects.AudioError, '未证实本任务生命周期'):
            controller.begin()
        before = len(self.robot.writes)
        with self.assertRaises(effects.AudioError):
            controller.stop_music()
        self.assertEqual(len(self.robot.writes), before)
        self.assertTrue(controller.record.exists())

    def test_short_default_pose_can_finish_with_owned_fresh_stationary_walk_feedback(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.playing = True
        controller.begin()
        self.robot.reset_states = [1]
        self.robot.default_auto_return = True
        self.robot.playing = False
        controller.stop_music()
        self.assertFalse(controller.record.exists())
        self.assertFalse(controller.task_running_seen)
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_default_pose_does_not_reuse_prior_running_or_force_walk_from_global_idle(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.playing = True
        controller.begin()
        self.robot.reset_states = [1]
        self.robot.playing = False
        with self.assertRaisesRegex(effects.AudioError, '未证实本任务生命周期'):
            controller.stop_music()
        self.assertTrue(controller.record.exists())
        self.assertFalse(controller.task_running_seen)
        self.assertEqual(self.robot.action, 1005)
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_next_segment_tracks_its_own_mode_task_after_factory_returns_to_walk(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.dance_auto_return = True
        self.robot.playing = True
        controller.begin()
        self.robot.now += 1
        controller.tick()
        self.robot.now += 1
        controller.tick()
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 2)
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005, 1005])
        self.assertFalse(controller.control_lost)

    def test_same_mode_external_task_before_final_walk_write_is_not_overridden(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.playing = True
        controller.begin()
        self.robot.playing = False
        set_mode = controller.set_mode
        spin = self.robot.spin
        walking = False
        spins = 0
        def mark_walk(action, cleanup=False):
            nonlocal walking
            walking = action == 1002
            return set_mode(action, cleanup)
        def external_before_write(*args, **kwargs):
            nonlocal spins
            if walking:
                spins += 1
                if spins >= 2:
                    self.robot.mc_task_id = 999999
            spin(*args, **kwargs)
        controller.set_mode = mark_walk
        self.robot.spin = external_before_write
        with self.assertRaisesRegex(effects.AudioError, '其他任务接管'):
            controller.stop_music()
        self.assertTrue(controller.record.exists())
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_end_before_first_preset_does_not_restore_over_an_external_mode_task(self):
        controller = self.controller()
        self.robot.playing = True
        client = controller.clients['SetMcAction']
        call = client.call_async
        def end_after_mode(request):
            future = call(request)
            if request.command.action.value == 1005:
                self.robot.playing = False
            return future
        client.call_async = end_after_mode
        controller.begin()
        self.assertIsNone(controller.task_id)
        self.robot.mc_task_id = 999999
        self.robot.spin()
        with self.assertRaisesRegex(effects.AudioError, '其他任务接管'):
            controller.stop_music()
        self.assertTrue(controller.record.exists())
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005])

    def test_end_during_next_mode_handoff_restores_its_own_mode_without_querying_old_preset(self):
        controller = self.controller()
        self.robot.global_query = True
        self.robot.dance_auto_return = True
        self.robot.playing = True
        controller.begin()
        client = controller.clients['SetMcAction']
        call = client.call_async
        def end_after_next_mode(request):
            future = call(request)
            if request.command.action.value == 1005:
                self.robot.playing = False
            return future
        client.call_async = end_after_next_mode
        self.robot.now += 1
        controller.tick()
        self.robot.now += 1
        controller.tick()
        controller.stop_music()
        self.assertFalse(controller.record.exists())
        self.assertEqual(sum(name == 'SetMcPresetMotion' for name, _ in self.robot.writes), 1)
        self.assertEqual([request.command.action.value for name, request in self.robot.writes
                          if name == 'SetMcAction'], [1005, 1005, 1002])
