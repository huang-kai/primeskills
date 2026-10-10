from pathlib import Path
import unittest
from unittest.mock import Mock

from primebuddy.settings import SetupError
from primebuddy.skill_manager import load_entry

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(ROOT, 'src/config.py')

    def test_old_installations_keep_full_song_until_reconfigured(self):
        self.assertEqual(self.module.playback_seconds({}), 0)

    def test_invalid_duration_is_rejected_before_any_playback(self):
        for value in (-1, 601, True, 1.5, '30', None):
            with self.subTest(value=value), self.assertRaises(SetupError):
                self.module.playback_seconds({'play_seconds': value})

    def test_setup_retries_invalid_input_and_preserves_other_settings(self):
        ui = Mock()
        ui.ask.side_effect = ['abc', '-1', '601', '1.5', '30']
        self.assertEqual(self.module.configure_playback(ui, {'keep': 'yes'}, 60),
                         {'keep': 'yes', 'play_seconds': 30})
        self.assertEqual(ui.info.call_count, 4)

    def test_reconfiguration_displays_saved_value_and_allows_full_song(self):
        ui = Mock()
        ui.ask.return_value = '0'
        config = {'play_seconds': 30}
        result = self.module.configure_playback(ui, config, 60)
        self.assertEqual(ui.ask.call_args.args[1], '30')
        self.assertEqual(result['play_seconds'], 0)
        self.assertEqual(config['play_seconds'], 30)
