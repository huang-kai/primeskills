"""点歌前四足起身；只使用模拟反馈，不连接机器人。"""
from pathlib import Path
from types import SimpleNamespace as Item
import tempfile
import unittest
from unittest.mock import Mock, patch

from primebuddy.skill_manager import load_entry
from primebuddy.voice_support.robot_audio import AudioError

module = load_entry(Path(__file__).resolve().parents[1], 'src/music_effects.py')


class PrepareStandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.effects = module.MusicEffects.__new__(module.MusicEffects)
        e = self.effects
        e.record = Path(self.temp.name) / 'restore.json'
        e.original_light_state = None
        e.stand_pending = False
        e.set_stand, e.wait_stand, e.require_resource = Mock(), Mock(), Mock()
        e.motion_received_at = e.light_received_at = 10
        self.clock = patch.object(module.time, 'monotonic', return_value=10)
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
        for action in (1, 110):
            self.state(action)
            e = self.effects
            e.set_stand.reset_mock()
            e.wait_stand.reset_mock()
            def submitted():
                self.assertTrue(e.record.exists())
                self.assertTrue(e.stand_pending)
            e.set_stand.side_effect = submitted
            e.prepare_stand()
            e.set_stand.assert_called_once_with()
            e.wait_stand.assert_called_once_with(after=10)
            self.assertEqual(e.stand_action, 101)
            self.assertFalse(e.record.exists())
            self.assertFalse(e.stand_pending)

    def test_standing_keeps_exact_current_mode_without_rise_request(self):
        for action in (101, 102, 201, 300):
            self.state(action)
            self.effects.prepare_stand()
            self.assertEqual(self.effects.stand_action, action)
        self.effects.set_stand.assert_not_called()
        self.effects.wait_stand.assert_not_called()
        self.assertFalse(self.effects.record.exists())

    def test_passive_biped_or_unknown_form_is_not_treated_as_quad_prone(self):
        for form in (0, 2):
            self.state(1)
            self.effects.motion_state.form_state.current_form = form
            with self.assertRaises(AudioError): self.effects.prepare_stand()
        self.effects.set_stand.assert_not_called()

    def test_unsafe_or_stale_feedback_cannot_start_rising(self):
        for kind in ('moving', 'fallen', 'lifted', 'held', 'stale', 'different_action', 'transition'):
            self.state(1)
            e = self.effects
            e.motion_received_at = 10
            if kind == 'moving': e.motion_state.is_moving = True
            if kind == 'fallen': e.motion_state.is_fallen = True
            if kind == 'lifted': e.motion_state.lift_state.is_lifted = True
            if kind == 'held': e.motion_state.held_state.is_held = True
            if kind == 'stale': e.motion_received_at = 7
            if kind == 'different_action': e.motion_state.action_info = Item(current_action=Item(value=110))
            if kind == 'transition': e.action.return_value.status.value = 200
            with self.subTest(kind=kind), self.assertRaises(AudioError): e.prepare_stand()
        e.set_stand.assert_not_called()

    def test_resource_failure_prevents_stand(self):
        self.state(1)
        self.effects.require_resource.side_effect = AudioError('缺动作资源')
        with self.assertRaises(AudioError): self.effects.prepare_stand()
        self.effects.set_stand.assert_not_called()

    def test_pose_change_during_resource_check_prevents_stand(self):
        self.state(1)
        def change(): self.state(110)
        self.effects.require_resource.side_effect = change
        with self.assertRaisesRegex(AudioError, '姿态已变化'): self.effects.prepare_stand()
        self.effects.set_stand.assert_not_called()

    def test_unknown_stand_result_preserves_record_and_does_not_retry(self):
        for failed in ('set_stand', 'wait_stand'):
            self.state(1)
            e = self.effects
            e.record.unlink(missing_ok=True)
            e.set_stand, e.wait_stand = Mock(), Mock()
            getattr(e, failed).side_effect = AudioError('超时')
            with self.assertRaises(AudioError): e.prepare_stand()
            self.assertTrue(e.record.exists())
            with self.assertRaisesRegex(AudioError, '记录保留'): e.stop_music()
            e.set_stand.assert_called_once()

    def test_existing_record_prevents_motion(self):
        self.state(1)
        self.effects.record.write_text('keep')
        with self.assertRaises(FileExistsError): self.effects.prepare_stand()
        self.effects.set_stand.assert_not_called()
        self.assertEqual(self.effects.record.read_text(), 'keep')

    def test_wait_requires_post_request_standing_feedback(self):
        for reason in ('old_frame', 'different_action'):
            self.state(101)
            e = self.effects
            e.select_posture(101)
            if reason == 'old_frame': e.motion_received_at = 9
            else: e.motion_state.action_info = Item(current_action=Item(value=1))
            with patch.object(module.time, 'monotonic', side_effect=[10, 10, 26]), self.assertRaises(AudioError):
                module.DanceController.wait_stand(e, after=10)
