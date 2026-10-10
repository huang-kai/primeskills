from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from primebuddy.skill_manager import load_entry
from primebuddy.voice_support.robot_audio import AudioError

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/effects_session.py')
        self.context = SimpleNamespace(runtime=Path('/runtime'))

    def test_disabled_effects_do_not_create_ros_node(self):
        with patch.object(self.module, 'EffectsRobot') as robot:
            with self.module.effects_session(self.context, False) as effects:
                self.assertIsNone(effects)
            robot.assert_not_called()

    def test_node_closed_and_effects_restored_even_when_song_cleanup_fails(self):
        with patch.object(self.module, 'EffectsRobot') as robot, patch.object(self.module, 'MusicEffects') as cls:
            with self.assertRaisesRegex(AudioError, '播放器关闭失败'):
                with self.module.effects_session(self.context, True):
                    raise AudioError('播放器关闭失败')
            cls.return_value.inspect.assert_called_once()
            cls.return_value.require_ready.assert_called_once()
            cls.return_value.prepare_stand.assert_not_called()
            cls.return_value.stop_music.assert_called_once()
            robot.return_value.close.assert_called_once()

    def test_failed_initialization_still_closes_ros_node(self):
        with patch.object(self.module, 'EffectsRobot') as robot, \
                patch.object(self.module, 'MusicEffects', side_effect=AudioError('存在恢复记录')):
            with self.assertRaisesRegex(AudioError, '恢复记录'):
                with self.module.effects_session(self.context, True):
                    self.fail('不得进入播放')
            robot.return_value.close.assert_called_once()

    def test_failed_restoration_is_reported_and_node_closed(self):
        with patch.object(self.module, 'EffectsRobot') as robot, patch.object(self.module, 'MusicEffects') as cls:
            cls.return_value.stop_music.side_effect = AudioError('恢复未完成')
            with self.assertRaisesRegex(AudioError, '恢复未完成'):
                with self.module.effects_session(self.context, True):
                    pass
            robot.return_value.close.assert_called_once()
