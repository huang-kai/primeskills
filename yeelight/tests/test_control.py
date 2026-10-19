"""易来主要业务：目标清单、状态核实和有限只读复核。"""
from pathlib import Path
import json
import unittest
from unittest.mock import call, patch
from helpers import load_entry



class LightControlTests(unittest.TestCase):
    def setUp(self):
        self.module = load_entry(Path(__file__).resolve().parents[1], 'src/skill.py')
        self.plan = {'tasks': [{}, {'args': {'power': True, 'scope': 'room', 'room': '厨房', 'device': ''}}]}

    def test_device_control_uses_inventory_and_one_write_before_reporting_verified_result(self):
        module = self.module
        args = {'power': True, 'scope': 'device', 'room': '客厅', 'device': '天际线灯'}
        with patch.object(module.mcp_client, 'LocalMcp') as mcp:
            reader = mcp.return_value.__enter__.return_value
            reader.call.side_effect = [
                {'status': 'success', 'result': {'entities': [
                    {'id': 'r1', 'type': 'room', 'name': '客厅'},
                    {'id': 'd1', 'type': 'device', 'name': '天际线灯', 'roomId': 'r1'}]}},
                {'status': 'success', 'result': {'verified': True, 'expectedValue': True,
                    'verifiedValue': True, 'entity': {'name': '天际线灯'}}}]
            result = module.set_power(**args, config={'house_id': 'test'})
            self.assertEqual(result, {'status': 'success', 'message': '天际线灯已打开。'})
            self.assertEqual(reader.call.call_args_list, [
                call('yeelight_home_list_entities', {'houseId': 'test', 'locale': 'zh-CN'}),
                call('yeelight_home_control_light', {'houseId': 'test', 'locale': 'zh-CN', 'action': 'power',
                    'value': True, 'targetType': 'device', 'targetName': '天际线灯', 'roomName': '客厅'})])

    def test_planning_context_is_readonly_names_without_ids_or_private_fields(self):
        module = self.module
        with patch.object(module.mcp_client, 'LocalMcp') as mcp:
            reader = mcp.return_value.__enter__.return_value
            reader.call.return_value = {'status': 'success', 'result': {'entities': [
                {'id': 'r1', 'type': 'room', 'name': '客厅'},
                {'id': 'd1', 'type': 'device', 'name': '天际线灯', 'roomId': 'r1', 'private': 'not-exported'}]}}
            value = module.context({'house_id': 'test'})
            self.assertEqual(value['devices'], [{'name': '天际线灯', 'room': '客厅'}])
            reader.call.assert_called_once_with('yeelight_home_list_entities', {'houseId': 'test', 'locale': 'zh-CN'})
            self.assertNotIn('not-exported', json.dumps(value))

    def test_light_results_require_requested_state(self):
        module = self.module
        args = self.plan['tasks'][1]['args']
        cases = [
            ({'verified': True, 'expectedValue': 'on', 'verifiedValue': 'on'}, 'success'),
            ({'verified': False, 'expectedValue': True, 'verifiedValue': True}, 'failed'),
            ({'verified': True, 'expectedValue': False, 'verifiedValue': False}, 'failed'),
            ({'verified': True, 'expectedValue': True, 'verifiedValue': False}, 'failed'),
        ]
        for result, status in cases:
            with self.subTest(result=result), patch.object(module.mcp_client, 'McpLightClient') as client:
                client.return_value.execute.return_value = {'status': 'success', 'result': result}
                actual = module.set_power(**args, config={'house_id': 'test'})
                self.assertEqual(actual['status'], status)
                if status == 'failed':
                    self.assertNotIn('已打开', actual['message'])
                    self.assertNotIn('已关闭', actual['message'])

    def test_light_auth_and_fuzzy_name_do_not_report_success(self):
        module = self.module
        for response, status in [({'status': 'auth_required'}, 'auth_required'),
                ({'status': 'clarification_required', 'suggestedIntent': {'device': '天际线灯'}}, 'clarification_required')]:
            with patch.object(module.mcp_client, 'McpLightClient') as client:
                client.return_value.execute.return_value = response
                actual = module.set_power(**self.plan['tasks'][1]['args'], config={'house_id': 'test'})
                self.assertEqual(actual['status'], status)

    def test_partial_light_write_is_only_read_back_not_retried(self):
        module = self.module
        args = {'power': False, 'scope': 'device', 'device': '天际线灯', 'room': '客厅'}
        with patch.object(module.mcp_client, 'McpLightClient') as client, \
                patch.object(module.mcp_client, 'LocalMcp') as mcp, patch.object(module.time, 'sleep'):
            client.return_value.execute.return_value = {'status': 'partial'}
            reader = mcp.return_value.__enter__.return_value
            reader.call.side_effect = [
                {'status': 'success', 'result': {'properties': {'online': True, 'power': True}}},
                {'status': 'success', 'result': {'properties': {'online': True, 'power': False}}}]
            result = module.set_power(**args, config={'house_id': 'test'})
            self.assertEqual(result['status'], 'success')
            client.return_value.execute.assert_called_once()
            self.assertEqual(reader.call.call_count, 2)
            self.assertTrue(all(c.args[0] == 'yeelight_home_get_state' for c in reader.call.call_args_list))

    def test_delayed_readback_is_bounded_and_offline_is_not_success(self):
        module = self.module
        args = {'power': False, 'scope': 'device', 'device': '天际线灯', 'room': '客厅'}
        with patch.object(module.mcp_client, 'McpLightClient') as client, \
                patch.object(module.mcp_client, 'LocalMcp') as mcp, patch.object(module.time, 'sleep'):
            client.return_value.execute.return_value = {'status': 'partial'}
            reader = mcp.return_value.__enter__.return_value
            reader.call.return_value = {'status': 'success', 'result': {'properties': {'online': False, 'power': False}}}
            result = module.set_power(**args, config={'house_id': 'test'})
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(reader.call.call_count, 3)
            client.return_value.execute.assert_called_once()
