"""原厂 RPC 的提交边界、明确拒绝与未知结果；无 ROS/设备调用。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.robot.transport import Robot
from chengyu_game.support import AudioError, Cancelled


def response(code=0, value=1, reason=0):
    return NS(header=NS(header=NS(code=code), status=NS(value=value, reason=reason,
                                                    FAILURE=2, INVALID=5, NOT_READY=100)))


class TransportCallTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.robot = Robot.__new__(Robot)
        self.robot.checkpoint = Mock()
        self.robot.spin = lambda seconds=.05, **kwargs: setattr(self, 'now', self.now + seconds)
        self.client = Mock()
        self.client.service_is_ready.return_value = True
        self.client.call_async.return_value = NS(done=lambda: True, result=lambda: response())
        patcher = patch('chengyu_game.robot.transport.time.monotonic', lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_callback_runs_only_immediately_before_call_async(self):
        order = []
        self.client.service_is_ready.side_effect = lambda: self.now >= .05
        self.client.call_async.side_effect = lambda request: (order.append('submit'),
            NS(done=lambda: True, result=lambda: response()))[1]
        self.robot.call(self.client, object(), before_submit=lambda: order.append('pending'))
        self.assertEqual(order, ['pending', 'submit'])

    def test_service_unready_or_cancelled_before_dispatch_never_marks_pending(self):
        mark = Mock()
        self.client.service_is_ready.return_value = False
        with self.assertRaises(AudioError):
            self.robot.call(self.client, object(), timeout=2, before_submit=mark)
        self.client.service_is_ready.return_value = True
        self.robot.checkpoint.side_effect = Cancelled('cancel before submit')
        with self.assertRaises(Cancelled):
            self.robot.call(self.client, object(), before_submit=mark)
        mark.assert_not_called()
        self.client.call_async.assert_not_called()

    def test_explicit_complete_rejection_has_distinct_audio_error_type(self):
        for result in (response(code=9), response(value=2)):
            self.client.call_async.return_value = NS(done=lambda: True, result=lambda: result)
            with self.subTest(result=result):
                with self.assertRaises(AudioError) as caught:
                    self.robot.call(self.client, object())
                self.assertEqual(type(caught.exception).__name__, 'RequestRejected')

    def test_unknown_or_incomplete_response_is_never_categorized_as_rejected(self):
        for result in (None, response(value=0), response(value=999), response(value=4),
                       NS(header=NS(header=NS(code=0), status=NS(value=2, reason=0))),
                       NS(header=NS(header=NS(code=9), status=NS(value=2)))):
            self.client.call_async.return_value = NS(done=lambda: True, result=lambda: result)
            with self.subTest(result=result):
                with self.assertRaises(AudioError) as caught:
                    self.robot.call(self.client, object())
                self.assertNotEqual(type(caught.exception).__name__, 'RequestRejected')

    def test_timeout_after_submit_keeps_unknown_result_without_retry(self):
        mark = Mock()
        self.client.call_async.return_value = NS(done=lambda: False)
        with self.assertRaisesRegex(AudioError, '结果未知'):
            self.robot.call(self.client, object(), timeout=2, before_submit=mark)
        mark.assert_called_once_with()
        self.client.call_async.assert_called_once()

    def test_legacy_call_without_callback_remains_supported(self):
        self.assertEqual(self.robot.call(self.client, object()).header.status.value, 1)



if __name__ == '__main__':
    unittest.main()
