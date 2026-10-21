"""使用两份用户 SDK 的实际定义校验请求和响应层级，不安装 ROS。"""
from pathlib import Path
from types import SimpleNamespace as Item
import unittest
from unittest.mock import Mock, patch

from helpers import load_entry

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
effects = load_entry(ROOT, 'src/effects/music_effects.py')


def message_type(name, definitions, cache):
    if name in cache:
        return cache[name]
    fields, constants = {}, {}
    for line in definitions[name].splitlines():
        declaration = line.split('#', 1)[0].strip()
        if not declaration:
            continue
        kind, field = declaration.split(maxsplit=1)
        if '=' in field:
            key, value = field.split('=', 1)
            constants[key.strip()] = int(value.strip(), 0)
        elif '[' in kind:
            fields[field] = list
        elif kind == 'string':
            fields[field] = str
        elif kind == 'bool':
            fields[field] = bool
        elif kind.startswith(('int', 'uint')):
            fields[field] = int
        elif kind.startswith('float'):
            fields[field] = float
        elif kind == 'builtin_interfaces/Time':
            fields[field] = lambda: Item(sec=0, nanosec=0)
        else:
            fields[field] = message_type(kind, definitions, cache)
    def init(self):
        for field, factory in fields.items():
            setattr(self, field, factory())
    result = type(name, (), dict(constants, __slots__=tuple(fields), __init__=init))
    cache[name] = result
    return result


class SdkContractTests(unittest.TestCase):
    def test_both_sdk_request_headers_and_response_status_paths(self):
        for directory, kind, stand in [('sdk_t1-v1.0.0.0', effects.MusicEffects, 201),
                                       ('primebot-q1-sdk', effects.Q1MusicEffects, 1002)]:
            with self.subTest(sdk=directory):
                sdk = WORKSPACE / directory / 'aimdk_msgs'
                if not sdk.exists():
                    self.skipTest('需要用户提供的两份 SDK 夹具')
                definitions = {path.stem: path.read_text() for path in sdk.rglob('*.msg')}
                controller = kind.__new__(kind)
                controller.robot = Mock()
                controller.music_playing = lambda: True
                controller.last_control_at = -1
                controller.types, controller.clients = {}, {}
                names = ['GetMcAction', 'SetMcAction', 'SetMcMotion', 'LedStripCommand']
                if directory == 'primebot-q1-sdk':
                    names.append('SetMcPresetMotion')
                for name in names:
                    request, response = next(sdk.rglob(name + '.srv')).read_text().split('---', 1)
                    all_definitions = dict(definitions, Request=request, Response=response)
                    cache = {}
                    service = Item(Request=message_type('Request', all_definitions, cache),
                                   Response=message_type('Response', all_definitions, cache))
                    controller.types[name] = service
                    reply = service.Response()
                    if hasattr(reply, 'response') and hasattr(reply.response, 'state'):
                        reply.response.state.value = 1
                    client = Mock()
                    client.service_is_ready.return_value = True
                    client.call_async.return_value.done.return_value = True
                    client.call_async.return_value.result.return_value = reply
                    controller.clients[name] = client
                    # Real slot definitions reject an incorrect header nesting.
                    controller.rpc(name, controller.request(name))
                controller.stand_action = stand
                controller.set_stand()
                sent = controller.clients['SetMcAction'].call_async.call_args.args[0]
                self.assertEqual(sent.command.action.value, stand)
                motion = controller.request('SetMcMotion')
                self.assertEqual((motion.MIMIC_QY, motion.EXECUTION), (1, 2))
                if directory == 'primebot-q1-sdk':
                    action = message_type('McAction', definitions, {})
                    self.assertEqual((action.BIPED_WALK_RUN, action.MIMIC), (1002, 1003))
