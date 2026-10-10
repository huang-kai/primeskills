"""执行远端播放器完整主循环；以假 Gst 时钟验证独立停歌与关闭反馈。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from primebuddy.skill_manager import load_entry

ROOT = Path(__file__).resolve().parents[1]


class PlayerLimitTests(unittest.TestCase):
    def play(self, limit, expected, eos_at=None):
        module = load_entry(ROOT, 'src/gst_player_process.py')
        clock = [0]
        state = [0]
        def pop(*args):
            clock[0] += 1
            if eos_at is not None and clock[0] == eos_at:
                return SimpleNamespace(type=2)
        def set_state(value):
            state[0] = value
        pipeline = Mock()
        pipeline.get_bus.return_value.timed_pop_filtered.side_effect = pop
        pipeline.set_state.side_effect = set_state
        pipeline.get_state.side_effect = lambda timeout: (None, state[0], None)
        pipeline.query_duration.return_value = (True, expected)
        pipeline.query_position.return_value = (True, 1)
        gst = SimpleNamespace(init=lambda _: None, parse_launch=lambda _: pipeline,
                              State=SimpleNamespace(PAUSED=1, PLAYING=2, NULL=0),
                              StateChangeReturn=SimpleNamespace(FAILURE=-1),
                              MessageType=SimpleNamespace(ERROR=1, EOS=2), Format=SimpleNamespace(TIME=1),
                              SECOND=1, MSECOND=.001, Structure=SimpleNamespace(new_from_string=lambda _: None))
        source = io.StringIO(json.dumps({'url': 'unused', 'seconds': expected, 'play_seconds': limit})+'\nplay\n')
        def select(*args):
            return ([source] if clock[0] == 1 else [], [], [])
        output = io.StringIO()
        with patch.dict('sys.modules', {'gi': SimpleNamespace(require_version=lambda *args: None),
                                       'gi.repository': SimpleNamespace(Gst=gst)}), \
                patch.object(module.sys, 'stdin', source), patch.object(module.signal, 'signal'), \
                patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(module.select, 'select', side_effect=select), redirect_stdout(output):
            module.main()
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1], {'event': 'closed', 'confirmed': True})
        self.assertEqual(state[0], 0)
        return events, clock[0]

    def test_remote_limit_stops_without_business_board_stop_command(self):
        events, ended_at = self.play(3, 120)
        self.assertIn({'event': 'ended', 'reason': 'limit'}, events)
        self.assertEqual(ended_at, 4)  # 首个时钟步确认开播，随后三秒停止。

    def test_full_song_uses_natural_end(self):
        events, _ = self.play(0, 10, eos_at=11)
        self.assertIn({'event': 'ended', 'reason': 'natural'}, events)

    def test_preview_early_end_is_still_an_error(self):
        events, _ = self.play(0, 120, eos_at=3)
        self.assertIn({'event': 'error'}, events)
        self.assertFalse(any(item['event'] == 'ended' for item in events))
