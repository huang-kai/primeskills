"""本地 stdio MCP：握手、工具发现、单次调用，不自动重试写入。"""
import json
from difflib import SequenceMatcher
import os
import selectors
import subprocess
import time
from .client import LightError


class LocalMcp:
    def __init__(self, executable='yeelight-home', profile=None, checkpoint=None):
        self.executable, self.profile, self.checkpoint = executable, profile, checkpoint
        self.process = None
        self.pending = bytearray()
        self.counter = 0

    def __enter__(self):
        args = [self.executable, 'mcp', 'serve', '--stdio']
        if self.profile:
            args.extend(['--profile', self.profile])
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL)
            self.selector = selectors.DefaultSelector()
            self.selector.register(self.process.stdout, selectors.EVENT_READ)
            self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                     'clientInfo': {'name': 't1-yeelight', 'version': '0.2.0'}})
            self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            tools = self.rpc('tools/list', {})
            self.tools = {tool['name'] for tool in tools['tools']}
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        data = json.dumps(message, ensure_ascii=False).encode() + b'\n'
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def rpc(self, method, params):
        if self.checkpoint:
            self.checkpoint()
        self.counter += 1
        request_id = self.counter
        self.send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.checkpoint:
                self.checkpoint()
            if b'\n' not in self.pending:
                if not self.selector.select(.2):
                    continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise LightError('MCP 连接已断开，执行结果尚未确认。')
                self.pending.extend(chunk)
                if len(self.pending) > 1024 * 1024:
                    raise LightError('MCP 响应过大。')
                continue
            line, _, tail = self.pending.partition(b'\n')
            self.pending = bytearray(tail)
            message = json.loads(line)
            if 'method' in message:
                # 不接受服务端发起的采样或其他反向操作。
                if 'id' in message:
                    self.send({'jsonrpc': '2.0', 'id': message['id'],
                               'error': {'code': -32601, 'message': 'Unsupported client method'}})
                continue
            if message.get('id') != request_id:
                raise LightError('MCP 响应 ID 不匹配。')
            if 'error' in message or not isinstance(message.get('result'), dict):
                raise LightError('MCP 调用失败，执行结果尚未确认。')
            return message['result']
        raise LightError('MCP 超时，执行结果尚未确认；不会自动重试。')

    def call(self, name, arguments):
        if name not in {'yeelight_home_get_home', 'yeelight_home_get_state',
                        'yeelight_home_control_light', 'yeelight_home_list_entities'} or name not in self.tools:
            raise LightError('MCP 工具不在已发现的灯控白名单内。')
        result = self.rpc('tools/call', {'name': name, 'arguments': arguments})
        response = result.get('structuredContent')
        if response is None:
            contents = [item['text'] for item in result.get('content', []) if item.get('type') == 'text']
            if len(contents) != 1:
                raise LightError('MCP 未返回唯一的结构化反馈。')
            response = json.loads(contents[0])
        if (not isinstance(response, dict) or response.get('contractVersion') != '1.0'
                or not isinstance(response.get('status'), str)):
            raise LightError('易来 MCP 反馈结构无效。')
        return response

    def __exit__(self, *_):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process.stdin.close()
            self.process.stdout.close()
        if hasattr(self, 'selector'):
            self.selector.close()


class McpLightClient:
    def __init__(self, house_id, executable='yeelight-home', profile=None, checkpoint=None):
        self.house_id, self.executable, self.profile = house_id, executable, profile
        self.checkpoint, self.dry_run = checkpoint, False

    def execute(self, text, intent, before_execute=None):
        from .intent import validate
        intent = validate(intent)
        if intent['intent'] != 'set_power':
            raise LightError('非灯控意图不得执行 MCP 写入。')
        args = {'houseId': self.house_id, 'locale': 'zh-CN', 'action': 'power',
                'value': intent['power'], 'targetType': intent['scope']}
        if intent['scope'] == 'home':
            args['targetId'] = self.house_id
        else:
            args['targetName'] = intent['device'] if intent['scope'] == 'device' else intent['room']
            if intent['scope'] == 'device' and intent['room']:
                args['roomName'] = intent['room']
        with LocalMcp(self.executable, self.profile, self.checkpoint) as mcp:
            inventory = mcp.call('yeelight_home_list_entities',
                                 {'houseId': self.house_id, 'locale': 'zh-CN'})
            if inventory['status'] != 'success':
                return inventory
            entities = inventory.get('result', {}).get('entities')
            if not isinstance(entities, list):
                raise LightError('无法读取真实房间列表，未执行灯控。')
            args = resolve_room_target(args, entities)
            suggestion = suggest_device(args, entities)
            if suggestion is not None:
                corrected = dict(intent, device=suggestion['name'])
                return {'contractVersion': '1.0', 'status': 'clarification_required',
                        'suggestedIntent': corrected}
            print('LIGHT_TARGET', json.dumps({key: args[key] for key in
                  ('targetType', 'targetId', 'targetName', 'roomName') if key in args},
                  ensure_ascii=False), flush=True)
            if before_execute is not None:
                resolved = intent
                if args['targetType'] == 'room' and args.get('targetId'):
                    room = next(entity['name'] for entity in entities
                                if entity.get('type') == 'room' and entity.get('id') == args['targetId'])
                    resolved = dict(intent, scope='room', room=room, device='')
                before_execute(resolved)
            return mcp.call('yeelight_home_control_light', args)


def resolve_room_target(arguments, entities):
    args = dict(arguments)
    name = args.get('targetName', '')
    rooms = [entity for entity in entities if isinstance(entity, dict)
             and entity.get('type') == 'room' and entity.get('id') and entity.get('name')]
    if args['targetType'] == 'device':
        # 真实同名设备优先，避免把某盏灯的指令扩大到整个房间。
        exact_device = any(isinstance(entity, dict) and entity.get('type') == 'device'
                           and entity.get('name') == name for entity in entities)
        if exact_device:
            return args
        matches = [room for room in rooms
                   if name in {room['name'] + '灯', room['name'] + '的灯', '灯', '灯光'}
                   and (not args.get('roomName') or args['roomName'] == room['name'])
                   and (name not in {'灯', '灯光'} or args.get('roomName'))]
    elif args['targetType'] == 'room':
        matches = [room for room in rooms if room['name'] == name]
    else:
        return args
    if len(matches) == 1:
        args['targetType'], args['targetId'] = 'room', matches[0]['id']
        args.pop('targetName', None)
        args.pop('roomName', None)
    return args



def suggest_device(arguments, entities):
    """只提出唯一近似名称供口头确认；不凭近似名称发出写入。"""
    if arguments.get('targetType') != 'device':
        return None
    name = arguments.get('targetName', '')
    if len(name) < 3:
        return None
    rooms = {entity.get('id'): entity.get('name') for entity in entities
             if isinstance(entity, dict) and entity.get('type') == 'room'}
    devices = [entity for entity in entities if isinstance(entity, dict)
               and entity.get('type') == 'device' and isinstance(entity.get('name'), str)
               and 0 < len(entity['name']) <= 50
               and not any(ord(c) < 32 for c in entity['name'])
               and (not arguments.get('roomName')
                    or rooms.get(entity.get('roomId')) == arguments['roomName'])]
    if any(entity['name'] == name for entity in devices):
        return None
    ranked = sorted(((SequenceMatcher(None, name, entity['name']).ratio(), entity)
                     for entity in devices), key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < .75:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .15:
        return None
    return ranked[0][1]
