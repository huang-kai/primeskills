"""当前 T1 实际转换表的回归；不连接机器人。"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.robot.motion_rules import NAMES, READ_RULES, select_quad_route, valid_quad_route, load_quad_route
from chengyu_game.support import AudioError


LIVE_EDGES = {'101': [102, 121], '102': [121], '121': [114, 101, 102], '114': [102, 121]}


class MotionRulesTests(unittest.TestCase):
    def test_current_firmware_requires_step_and_correct_return_to_each_initial_mode(self):
        self.assertEqual(select_quad_route(LIVE_EDGES, 101), {'enter': [121, 114], 'return': [121, 101]})
        self.assertEqual(select_quad_route(LIVE_EDGES, 102), {'enter': [121, 114], 'return': [102]})

    def test_direct_path_is_used_only_when_both_edges_are_present(self):
        edges = {'101': [114], '114': [101], '102': [101]}
        self.assertEqual(select_quad_route(edges, 101), {'enter': [114], 'return': [101]})
        with self.assertRaises(AudioError):
            select_quad_route({'101': [114]}, 101)

    def test_step_requires_cancel_return_and_never_plans_other_actions(self):
        with self.assertRaises(AudioError):
            select_quad_route({**LIVE_EDGES, '121': [114]}, 102)
        with self.assertRaises(AudioError):
            select_quad_route({'101': [103], '103': [114], '114': [101]}, 101)
        with self.assertRaisesRegex(AudioError, '自然结束'):
            select_quad_route({**LIVE_EDGES, '102': []}, 101)

    def test_persisted_route_rejects_unrelated_modes_and_bool_ids(self):
        for route in ({'enter': [103, 114], 'return': [101]}, {'enter': [114], 'return': [300]},
                      {'enter': [114], 'return': [101], 'extra': True}):
            self.assertFalse(valid_quad_route(route, 101))
        self.assertFalse(valid_quad_route({'enter': [114], 'return': [True]}, True))

    def test_reader_handles_actual_mc_executable_and_outputs_only_allowed_edges(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / 'proc' / '42'
            proc.mkdir(parents=True)
            (proc / 'exe').symlink_to('/robot/software/mc/bin/mc_app_main')
            (proc / 'environ').write_bytes(b'PRIVATE_VALUE=do-not-output\0ROBOT_CFG=qd1_t2d0\0')
            config = root / 'cfg' / 'qd1_t2d0'
            config.mkdir(parents=True)
            rules = {NAMES[int(key)]: {'next_actions': [NAMES[value] for value in values] + ['OTHER_ACTION']}
                     for key, values in LIVE_EDGES.items()}
            (config / 'action_ruler.yaml').write_text(yaml.safe_dump({'action_rules': rules}))
            source = READ_RULES.replace("Path('/proc')", 'Path(' + repr(str(root / 'proc')) + ')')
            source = source.replace("Path('/robot/software/mc/bin/cfg/robot')", 'Path(' + repr(str(root / 'cfg')) + ')')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(compile(source, '<readonly-rule-test>', 'exec'), {})
            self.assertEqual({key: set(value) for key, value in json.loads(output.getvalue()).items()},
                             {key: set(value) for key, value in LIVE_EDGES.items()})
            self.assertNotIn('PRIVATE', output.getvalue())

    def test_failed_or_malformed_reader_never_falls_back_to_sdk_guesses(self):
        with patch('chengyu_game.robot.motion_rules.host_command', return_value=['fixture']), \
                patch('chengyu_game.robot.motion_rules.subprocess.run', side_effect=OSError):
            with self.assertRaises(AudioError):
                load_quad_route('/tmp/fixture', 102)


if __name__ == '__main__':
    unittest.main()
