"""姿态前置检查、音乐联动与异常恢复。"""
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from helpers import load_entry

from types import SimpleNamespace as Item


music_effects_module = load_entry(Path(__file__).resolve().parents[1], 'src/effects/music_effects.py')


AudioError = music_effects_module.AudioError
MODULE = music_effects_module.__name__


DanceController, MusicEffects = music_effects_module.DanceController, music_effects_module.MusicEffects


HAPPY_EMOTION_IDS = music_effects_module.HAPPY_EMOTION_IDS


class MusicEffectsTests(unittest.TestCase):
    def effects(self, record):
        effects = MusicEffects.__new__(MusicEffects)
        effects.robot = Mock()
        effects.record = record
        effects.original_light_state = effects.last_owned_light_state = None
        effects.restore_failed = False
        effects.stand_pending = False
        effects.motion_state = SimpleNamespace(is_moving=False, is_fallen=False)
        effects.motion_received_at = 10
        effects.emotion_submitted = False
        effects.play_emotion = Mock()
        effects.require_stand = Mock(return_value=(254, 178, 155, 100, 4000))
        effects.start = Mock()
        effects.restore_stand = Mock()
        effects.restore_light = Mock()
        return effects

    def test_one_random_happy_emotion_per_song_and_empty_on_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp)/'restore.json')
            with patch(MODULE + '.random.choice', return_value=19) as choose:
                effects.begin()
            choose.assert_called_once_with(HAPPY_EMOTION_IDS)
            effects.play_emotion.assert_called_once_with(19)
            effects.stop_music()
            self.assertEqual(effects.play_emotion.call_args.args, (13,))
            self.assertEqual(effects.play_emotion.call_args.kwargs, {'cleanup': True})
            self.assertFalse(effects.record.exists())

    def test_emotion_start_timeout_still_attempts_empty_emotion(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp)/'restore.json')
            effects.play_emotion.side_effect = [AudioError('emotion timeout'), None]
            with self.assertRaisesRegex(AudioError, 'emotion timeout'):
                effects.begin()
            effects.stop_music()
            self.assertEqual(effects.play_emotion.call_args.args, (13,))
            effects.restore_stand.assert_called_once()
            effects.restore_light.assert_called_once()

    def test_light_restore_failure_does_not_skip_empty_emotion(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp)/'restore.json')
            effects.begin()
            effects.restore_light.side_effect = AudioError('light failed')
            with self.assertRaisesRegex(AudioError, 'light failed'):
                effects.stop_music()
            self.assertEqual(effects.play_emotion.call_args.args, (13,))
            self.assertTrue(effects.record.exists())

    def test_sdk_request_uses_looping_emotion_mode(self):
        effects = MusicEffects.__new__(MusicEffects)
        effects.robot, effects.emotion_client = Mock(), Mock()
        request = SimpleNamespace(header=SimpleNamespace(header=SimpleNamespace(stamp=None)))
        modules = {'aimdk_msgs': SimpleNamespace(), 'aimdk_msgs.srv': SimpleNamespace(
            PlayEmotion=SimpleNamespace(Request=lambda: request))}
        with patch.dict(sys.modules, modules):
            effects.play_emotion(18)
        self.assertEqual((request.type, request.emotion_ids, request.loop_count), ('emotion', [18], 0))
        effects.robot.call.assert_called_once_with(effects.emotion_client, request, cancellable=True)

    def test_motion_request_supports_deployed_schema_without_operation(self):
        class Request:
            __slots__ = ('source', 'motion', 'type', 'interrupt')
            MIMIC_QY = 1
        class SdkRequest(Request):
            __slots__ = ('operation',)
            EXECUTION = 2
        for request in (Request(), SdkRequest()):
            if isinstance(request, SdkRequest): request.operation = 0
            with self.subTest(schema=type(request).__name__):
                effects = DanceController.__new__(DanceController)
                effects.request = Mock(return_value=request)
                effects.rpc = Mock(return_value=SimpleNamespace(response=SimpleNamespace(task_id=3)))
                effects.action = Mock(return_value=SimpleNamespace(
                    current_action=SimpleNamespace(value=119), status=SimpleNamespace(value=100)))
                effects.start()
                self.assertEqual((request.source, request.motion, request.type, request.interrupt),
                                 ('sdk_rpc', 'QUAD_MIMIC', 1, False))
                if isinstance(request, SdkRequest): self.assertEqual(request.operation, 2)

    def test_unknown_seated_or_dancing_posture_is_not_an_entry(self):
        for action in (2, 111, 119, 208, 999):
            effects = DanceController.__new__(DanceController)
            with self.subTest(action=action), self.assertRaisesRegex(AudioError, '仅接受'):
                effects.select_posture(action)

    def test_biped_music_uses_biped_motion_and_restores_same_stand(self):
        for standing in (201, 300):
            effects = DanceController.__new__(DanceController)
            effects.select_posture(standing)
            self.assertEqual(effects.motion_name, 'BIPED_MIMIC_POPPING')
            request = SimpleNamespace(source='', motion='', type=0, interrupt=True, MIMIC_QY=1)
            effects.request = Mock(return_value=request)
            effects.rpc = Mock(return_value=SimpleNamespace(response=SimpleNamespace(task_id=7)))
            effects.action = Mock(return_value=SimpleNamespace(
                current_action=SimpleNamespace(value=208), status=SimpleNamespace(value=100)))
            effects.start()
            self.assertEqual(request.motion, 'BIPED_MIMIC_POPPING')
            self.assertFalse(request.interrupt)
            effects.motion_state = SimpleNamespace(is_moving=True, is_fallen=False)
            effects.motion_received_at = 10
            effects.set_stand, effects.wait_stand = Mock(), Mock()
            with patch(MODULE + '.time.monotonic', return_value=10):
                effects.restore_stand()
            self.assertEqual(effects.stand_action, 300)
            effects.set_stand.assert_called_once_with(cleanup=True)

    def test_biped_dance_completion_restarts_without_changing_to_quad(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp) / 'restore.json')
            effects.select_posture(300)
            effects.begin()
            effects.next_motion_check_at = 0
            effects.next_color_at = 100
            effects.action = Mock(return_value=SimpleNamespace(
                current_action=SimpleNamespace(value=300), status=SimpleNamespace(value=100)))
            with patch(MODULE + '.time.monotonic', return_value=10):
                effects.tick()
            self.assertEqual(effects.start.call_count, 2)
            self.assertEqual(effects.stand_action, 300)
            import json
            self.assertEqual(json.loads(effects.record.read_text())['motion'], 'BIPED_MIMIC_POPPING')

    def test_uninterruptible_biped_resource_is_rejected(self):
        effects = DanceController.__new__(DanceController)
        effects.select_posture(300)
        effects.request = Mock()
        effects.rpc = Mock(return_value=SimpleNamespace(motion=[SimpleNamespace(
            tag='BIPED_MIMIC_POPPING', intr_info=SimpleNamespace(intr_type=SimpleNamespace(value=0)))]))
        with self.assertRaisesRegex(AudioError, '自由打断'):
            effects.require_resource()

    def test_expired_or_fallen_feedback_never_restarts_dance(self):
        for fallen, received in ((False, 7), (True, 10)):
            with tempfile.TemporaryDirectory() as temp:
                effects = self.effects(Path(temp) / 'restore.json')
                effects.begin()
                effects.next_motion_check_at = 0
                effects.motion_state.is_fallen = fallen
                effects.motion_received_at = received
                effects.action = Mock(return_value=SimpleNamespace(
                    current_action=SimpleNamespace(value=102), status=SimpleNamespace(value=100)))
                with patch(MODULE + '.time.monotonic', return_value=10), self.assertRaises(AudioError):
                    effects.tick()
                effects.start.assert_called_once()  # 只有 begin 的首次提交。

    def test_external_change_from_biped_to_quad_is_not_overridden(self):
        effects = DanceController.__new__(DanceController)
        effects.select_posture(300)
        effects.motion_state = SimpleNamespace(is_moving=False, is_fallen=False)
        effects.motion_received_at = 10
        effects.action = Mock(return_value=SimpleNamespace(
            current_action=SimpleNamespace(value=101), status=SimpleNamespace(value=100)))
        effects.set_stand = Mock()
        with patch(MODULE + '.time.monotonic', return_value=10):
            with self.assertRaisesRegex(AudioError, '其他模式'):
                effects.restore_stand()
        effects.set_stand.assert_not_called()

    def test_completed_motion_in_stationary_quad_mode_can_restore_stand(self):
        effects = DanceController.__new__(DanceController)
        effects.action = Mock(return_value=SimpleNamespace(
            current_action=SimpleNamespace(value=102), status=SimpleNamespace(value=100)))
        effects.motion_state = SimpleNamespace(is_moving=False, is_fallen=False)
        effects.motion_received_at = 10
        effects.set_stand, effects.wait_stand = Mock(), Mock()
        with patch(MODULE + '.time.monotonic', return_value=10):
            effects.restore_stand()
        effects.set_stand.assert_called_once_with(cleanup=True)
        effects.wait_stand.assert_called_once()

    def test_moving_quad_mode_is_not_overridden(self):
        effects = DanceController.__new__(DanceController)
        effects.action = Mock(return_value=SimpleNamespace(
            current_action=SimpleNamespace(value=102), status=SimpleNamespace(value=100)))
        effects.motion_state = SimpleNamespace(is_moving=True, is_fallen=False)
        effects.motion_received_at = 10
        effects.set_stand = Mock()
        with patch(MODULE + '.time.monotonic', return_value=10),\
             self.assertRaises(AudioError):
            effects.restore_stand()
        effects.set_stand.assert_not_called()

    def test_music_restarts_completed_motion_but_does_not_interrupt_running_motion(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp)/'restore.json')
            effects.begin()
            effects.next_motion_check_at = 0
            effects.next_color_at = 100
            effects.action = Mock(return_value=SimpleNamespace(
                current_action=SimpleNamespace(value=119), status=SimpleNamespace(value=100)))
            effects.restore_stand = Mock()
            with patch(MODULE + '.time.monotonic', return_value=10):
                effects.tick()
            effects.start.assert_called_once()
            effects.action.return_value.current_action.value = 102
            with patch(MODULE + '.time.monotonic', return_value=12):
                effects.tick()
            self.assertEqual(effects.start.call_count, 2)
            effects.restore_stand.assert_called_once()

    def test_light_restore_waits_for_pending_rgb_callback(self):
        effects = DanceController.__new__(DanceController)
        original, owned = (254, 178, 155, 100, 4000), (255, 45, 0, 45, 500)
        effects.light_state, effects.light_received_at = (255, 0, 0, 60, 500), 10
        spins = [0]
        def spin(**kwargs):
            spins[0] += 1
            if spins[0] == 2:
                effects.light_state = owned
        effects.robot = Mock()
        effects.robot.spin.side_effect = spin
        def light(state, cleanup):
            effects.light_state, effects.light_received_at = state, 10
        effects.light = Mock(side_effect=light)
        with patch(MODULE + '.time.monotonic', return_value=10):
            effects.restore_light(original, owned)
        effects.light.assert_called_once_with(original, cleanup=True)

    def test_other_light_owner_is_not_overridden_after_bounded_wait(self):
        effects = DanceController.__new__(DanceController)
        effects.light_state, effects.light_received_at = (17, 255, 255, 255, 0), 10
        effects.robot, effects.light = Mock(), Mock()
        with patch(MODULE + '.time.monotonic', side_effect=[10, 10, 14]),\
             self.assertRaises(AudioError):
            effects.restore_light((254, 178, 155, 100, 4000), (255, 45, 0, 45, 500))
        effects.light.assert_not_called()

    def test_failed_restore_keeps_record_and_cleanup_does_not_repeat_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'restore.json'
            effects = self.effects(record)
            effects.begin()
            effects.restore_stand.side_effect = AudioError('stand not restored')
            with self.assertRaisesRegex(AudioError, 'stand not restored'):
                effects.stop_music()
            with self.assertRaisesRegex(AudioError, '未重复发送控制'):
                effects.stop_music()
            self.assertTrue(record.exists())
            effects.restore_stand.assert_called_once()
            effects.restore_light.assert_called_once()

    def test_existing_record_is_preserved_without_motion(self):
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp) / 'restore.json'
            record.write_text('existing')
            effects = self.effects(record)
            with self.assertRaises(FileExistsError):
                effects.begin()
            effects.stop_music()
            self.assertEqual(record.read_text(), 'existing')
            self.assertIsNone(effects.original_light_state)
            effects.start.assert_not_called()
            effects.restore_stand.assert_not_called()


prepare_stand_module = load_entry(Path(__file__).resolve().parents[1], 'src/effects/music_effects.py')


class PrepareStandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.effects = prepare_stand_module.MusicEffects.__new__(prepare_stand_module.MusicEffects)
        e = self.effects
        e.robot = Mock()
        e.record = Path(self.temp.name) / 'restore.json'
        e.original_light_state = None
        e.stand_pending = False
        e.set_stand, e.wait_stand, e.require_resource = Mock(), Mock(), Mock()
        e.motion_received_at = e.light_received_at = 10
        self.clock = patch.object(prepare_stand_module.time, 'monotonic', return_value=10)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def state(self, action, form=None):
        e = self.effects
        info = Item(current_action=Item(value=action), status=Item(value=100))
        e.action = Mock(return_value=info)
        e.motion_state = Item(is_moving=False, is_fallen=False, action_info=info,
            form_state=Item(current_form=form or (2 if action in (201, 300) else 1)),
            lift_state=Item(is_lifted=False), held_state=Item(is_held=False))

    def test_prone_quad_stands_once_and_clears_record_after_confirmation(self):
        for action in (110,):
            self.state(action)
            e = self.effects
            e.set_stand.reset_mock()
            e.wait_stand.reset_mock()
            def submitted():
                self.assertTrue(e.record.exists())
                self.assertTrue(e.stand_pending)
            e.set_stand.side_effect = submitted
            e.prepare()
            e.set_stand.assert_called_once_with()
            e.wait_stand.assert_called_once_with(after=10)
            self.assertEqual(e.stand_action, 101)
            self.assertFalse(e.record.exists())
            self.assertFalse(e.stand_pending)

    def test_standing_keeps_exact_current_mode_without_rise_request(self):
        for action in (101, 102, 201, 300):
            self.state(action)
            self.effects.prepare()
            self.assertEqual(self.effects.stand_action, 300 if action == 201 else action)
        self.effects.set_stand.assert_not_called()
        self.effects.wait_stand.assert_not_called()
        self.assertFalse(self.effects.record.exists())

    def test_passive_biped_or_unknown_form_is_not_treated_as_quad_prone(self):
        for form in (0, 2):
            self.state(1)
            self.effects.motion_state.form_state.current_form = form
            with self.assertRaises(AudioError): self.effects.prepare()
        self.effects.set_stand.assert_not_called()

    def test_unsafe_or_stale_feedback_cannot_start_rising(self):
        for kind in ('moving', 'fallen', 'lifted', 'held', 'stale', 'different_action', 'transition'):
            self.state(110)
            e = self.effects
            e.motion_received_at = 10
            if kind == 'moving': e.motion_state.is_moving = True
            if kind == 'fallen': e.motion_state.is_fallen = True
            if kind == 'lifted': e.motion_state.lift_state.is_lifted = True
            if kind == 'held': e.motion_state.held_state.is_held = True
            if kind == 'stale': e.motion_received_at = 7
            if kind == 'different_action': e.motion_state.action_info = Item(current_action=Item(value=101))
            if kind == 'transition': e.action.return_value.status.value = 200
            with self.subTest(kind=kind), self.assertRaises(AudioError): e.prepare()
        e.set_stand.assert_not_called()

    def test_resource_failure_prevents_stand(self):
        self.state(110)
        self.effects.require_resource.side_effect = AudioError('缺动作资源')
        with self.assertRaises(AudioError): self.effects.prepare()
        self.effects.set_stand.assert_not_called()

    def test_pose_change_during_resource_check_prevents_stand(self):
        self.state(110)
        def change(): self.state(101)
        self.effects.require_resource.side_effect = change
        with self.assertRaisesRegex(AudioError, '姿态已变化'): self.effects.prepare()
        self.effects.set_stand.assert_not_called()

    def test_unknown_stand_result_preserves_record_and_does_not_retry(self):
        for failed in ('set_stand', 'wait_stand'):
            self.state(110)
            e = self.effects
            e.record.unlink(missing_ok=True)
            e.set_stand, e.wait_stand = Mock(), Mock()
            getattr(e, failed).side_effect = AudioError('超时')
            with self.assertRaises(AudioError): e.prepare()
            self.assertTrue(e.record.exists())
            with self.assertRaisesRegex(AudioError, '记录保留'): e.stop_music()
            e.set_stand.assert_called_once()

    def test_existing_record_prevents_motion(self):
        self.state(110)
        self.effects.record.write_text('keep')
        with self.assertRaises(FileExistsError): self.effects.prepare()
        self.effects.set_stand.assert_not_called()
        self.assertEqual(self.effects.record.read_text(), 'keep')

    def test_wait_requires_post_request_standing_feedback(self):
        for reason in ('old_frame', 'different_action'):
            self.state(101)
            e = self.effects
            e.select_posture(101)
            if reason == 'old_frame': e.motion_received_at = 9
            else: e.motion_state.action_info = Item(current_action=Item(value=1))
            with patch.object(prepare_stand_module.time, 'monotonic', side_effect=[10, 10, 26]), self.assertRaises(AudioError):
                prepare_stand_module.DanceController.wait_stand(e, after=10)


ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/effects/session.py')
        self.robot = Mock()
        self.device = Item(series='T1')

    def test_disabled_effects_do_not_create_controller(self):
        with patch.object(self.module, 'MusicEffects') as cls:
            with self.module.effects_session(self.robot, self.device, Path('/runtime/record'), False) as effects:
                self.assertIsNone(effects)
            cls.assert_not_called()

    def test_effects_restore_and_close_on_song_failure(self):
        with patch.object(self.module, 'MusicEffects') as cls:
            with self.assertRaisesRegex(AudioError, '播放器关闭失败'):
                with self.module.effects_session(self.robot, self.device, Path('/runtime/record'), True):
                    raise AudioError('播放器关闭失败')
            cls.return_value.stop_music.assert_called_once()
            cls.return_value.close.assert_called_once()

    def test_q1_assembles_its_own_effects(self):
        playing = lambda: True
        with patch.object(self.module, 'Q1MusicEffects') as q1, patch.object(self.module, 'MusicEffects') as t1:
            with self.module.effects_session(self.robot, Item(series='Q1'), Path('/runtime/record'), True,
                                             music_playing=playing):
                pass
            q1.assert_called_once_with(self.robot, Path('/runtime/record'), music_playing=playing)
            t1.assert_not_called()
