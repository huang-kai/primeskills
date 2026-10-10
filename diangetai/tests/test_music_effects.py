from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from primebuddy.skill_manager import load_entry
from primebuddy.voice_support.robot_audio import AudioError

module = load_entry(Path(__file__).resolve().parents[1], 'src/music_effects.py')
MODULE = module.__name__
DanceController, MusicEffects = module.DanceController, module.MusicEffects
HAPPY_EMOTION_IDS = module.HAPPY_EMOTION_IDS


class MusicEffectsTests(unittest.TestCase):
    def effects(self, record):
        effects = MusicEffects.__new__(MusicEffects)
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
                effects.begin_music()
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
                effects.begin_music()
            effects.stop_music()
            self.assertEqual(effects.play_emotion.call_args.args, (13,))
            effects.restore_stand.assert_called_once()
            effects.restore_light.assert_called_once()

    def test_light_restore_failure_does_not_skip_empty_emotion(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp)/'restore.json')
            effects.begin_music()
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
            self.assertEqual(effects.stand_action, standing)
            effects.set_stand.assert_called_once_with(cleanup=True)

    def test_biped_dance_completion_restarts_without_changing_to_quad(self):
        with tempfile.TemporaryDirectory() as temp:
            effects = self.effects(Path(temp) / 'restore.json')
            effects.select_posture(300)
            effects.begin_music()
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
                effects.begin_music()
                effects.next_motion_check_at = 0
                effects.motion_state.is_fallen = fallen
                effects.motion_received_at = received
                effects.action = Mock(return_value=SimpleNamespace(
                    current_action=SimpleNamespace(value=102), status=SimpleNamespace(value=100)))
                with patch(MODULE + '.time.monotonic', return_value=10), self.assertRaises(AudioError):
                    effects.tick()
                effects.start.assert_called_once()  # 只有 begin_music 的首次提交。

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
            effects.begin_music()
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
            effects.begin_music()
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
                effects.begin_music()
            effects.stop_music()
            self.assertEqual(record.read_text(), 'existing')
            self.assertIsNone(effects.original_light_state)
            effects.start.assert_not_called()
            effects.restore_stand.assert_not_called()


if __name__ == '__main__':
    unittest.main()
