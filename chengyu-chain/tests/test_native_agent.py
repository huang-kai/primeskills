"""宿主模式复用游戏 ROS 节点，仅查询原厂 Agent 模式。"""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from chengyu_game.robot import native_agent
from chengyu_game.support import AudioError, Cancelled


class PropertyId:
    AGENT_PROPERTY_RUN_MODE = 2

    def __init__(self, value):
        self.value = value


class GetAgentPropertiesRequest:
    @staticmethod
    def Request():
        return NS(header=NS(header=NS(stamp=None)), property_ids=[])


class AgentModeTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, {
            'aimdk_msgs': NS(), 'aimdk_msgs.msg': NS(AgentPropertyIdType=PropertyId),
            'aimdk_msgs.srv': NS(GetAgentPropertiesRequest=GetAgentPropertiesRequest)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.response = NS(header=NS(header=NS(code=0), status=NS(value=1)),
                           contents=NS(properties=[NS(key=NS(value=2), value='only_voice')]))
        self.client = object()
        self.node = NS(create_client=Mock(return_value=self.client), destroy_client=Mock(),
                       get_clock=lambda: NS(now=lambda: NS(to_msg=lambda: 'stamp')))
        self.robot = NS(node=self.node, call=Mock(return_value=self.response))

    def test_read_uses_existing_node_and_get_only_without_subprocess(self):
        with patch.object(native_agent.subprocess, 'run') as subprocess_run:
            self.assertEqual(native_agent.read_agent_mode(self.robot), 'only_voice')
        subprocess_run.assert_not_called()
        self.node.create_client.assert_called_once_with(
            GetAgentPropertiesRequest, '/aimdk_5Fmsgs/srv/GetAgentPropertiesRequest')
        self.node.destroy_client.assert_called_once_with(self.client)
        args, options = self.robot.call.call_args
        self.assertIs(args[0], self.client)
        self.assertEqual(args[1].header.header.stamp, 'stamp')
        self.assertEqual([item.value for item in args[1].property_ids], [2])
        self.assertEqual(options, {'timeout': 20})

    def test_normal_mode_is_read_without_changing_it(self):
        self.response.contents.properties[0].value = 'normal'
        self.assertEqual(native_agent.read_agent_mode(self.robot), 'normal')
        self.assertEqual(self.robot.call.call_count, 1)

    def test_enum_mismatch_fails_before_client_or_rpc(self):
        with patch.object(PropertyId, 'AGENT_PROPERTY_RUN_MODE', 9), self.assertRaises(AudioError):
            native_agent.read_agent_mode(self.robot)
        self.node.create_client.assert_not_called()
        self.robot.call.assert_not_called()

    def test_failed_or_ambiguous_response_is_rejected_and_client_destroyed(self):
        valid = NS(key=NS(value=2), value='only_voice')
        for code, status, properties in ((1, 1, [valid]), (0, 0, [valid]), (0, 1, []),
                (0, 1, [valid, valid]), (0, 1, [NS(key=NS(value=2), value='unknown')])):
            with self.subTest(code=code, status=status, properties=len(properties)):
                self.response.header.header.code = code
                self.response.header.status.value = status
                self.response.contents.properties = properties
                self.node.destroy_client.reset_mock()
                with self.assertRaises(AudioError):
                    native_agent.read_agent_mode(self.robot)
                self.node.destroy_client.assert_called_once_with(self.client)

    def test_rpc_failure_or_cancel_destroys_client_without_retry(self):
        for error in (AudioError('查询失败'), Cancelled('取消')):
            with self.subTest(error=type(error).__name__):
                self.robot.call.reset_mock(side_effect=True)
                self.robot.call.side_effect = error
                self.node.destroy_client.reset_mock()
                with self.assertRaises(type(error)):
                    native_agent.read_agent_mode(self.robot)
                self.assertEqual(self.robot.call.call_count, 1)
                self.node.destroy_client.assert_called_once_with(self.client)


if __name__ == '__main__':
    unittest.main()
