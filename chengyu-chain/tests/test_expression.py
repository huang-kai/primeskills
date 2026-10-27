"""表情只走 ROS；未提交与未知结果继续由原厂通信层区分。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.robot.expression import play_expression
from chengyu_game.robot.transport import RequestRejected
from chengyu_game.support import AudioError, Cancelled


class ExpressionTests(unittest.TestCase):
    def setUp(self):
        self.robot = NS(call=Mock())
        self.client, self.mark = object(), Mock()
        self.request = NS(type='emotion', emotion_ids=[90], priority=0, loop_count=1)

    def play(self, series='T1', cleanup=False):
        return play_expression(self.robot, self.client, self.request, series,
                               cleanup=cleanup, before_submit=self.mark)

    def test_t1_and_q1_use_existing_ros_client_without_http_or_ssh(self):
        for series in ('T1', 'Q1'):
            with self.subTest(series=series), patch('subprocess.Popen') as process, \
                    patch('urllib.request.build_opener') as http:
                self.robot.call.reset_mock()
                self.request.emotion_ids = [90 if series == 'T1' else 3003]
                self.assertIs(self.play(series), self.robot.call.return_value)
                self.robot.call.assert_called_once_with(self.client, self.request, cancellable=True,
                                                       before_submit=self.mark)
                process.assert_not_called()
                http.assert_not_called()

    def test_pending_marker_is_delegated_to_actual_ros_submission(self):
        self.play()
        self.mark.assert_not_called()
        self.assertIs(self.robot.call.call_args.kwargs['before_submit'], self.mark)

    def test_ros_timeout_cancellation_and_rejection_never_retry_or_switch_transport(self):
        for error in (AudioError('请求超时，结果未知'), Cancelled('cancelled'), RequestRejected('refused')):
            with self.subTest(error=type(error).__name__), patch('subprocess.Popen') as process, \
                    patch('urllib.request.build_opener') as http:
                self.robot.call.reset_mock()
                self.robot.call.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    self.play()
                self.assertIs(caught.exception, error)
                self.robot.call.assert_called_once()
                process.assert_not_called()
                http.assert_not_called()

    def test_cleanup_keeps_ros_and_can_finish_after_cancellation(self):
        self.request.emotion_ids = [13]
        self.play(cleanup=True)
        self.robot.call.assert_called_once_with(self.client, self.request, cancellable=False,
                                               before_submit=self.mark)

    def test_unapproved_t1_payload_never_submits(self):
        for ids in ([999], [90, 13], []):
            self.request.emotion_ids = ids
            with self.subTest(ids=ids), self.assertRaises(AudioError):
                self.play()
        self.robot.call.assert_not_called()
        self.mark.assert_not_called()


if __name__ == '__main__':
    unittest.main()
