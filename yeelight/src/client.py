"""通过 stdin JSON 调用已安装的 Yeelight Runtime，不经过 shell。"""
import json
import subprocess
import uuid
from .intent import validate

class LightError(Exception):
    pass


def build_request(text, intent, house_id, dry_run=False):
    validate(intent)
    if intent['intent'] != 'set_power' or not house_id.strip():
        raise LightError('执行请求必须有明确目标和家庭 ID。')
    params = {'houseId': house_id, 'power': intent['power'], 'nodeType': intent['scope']}
    if intent['room']:
        params['roomName'] = intent['room']
    if intent['device']:
        params['deviceName'] = intent['device']
    if intent['scope'] == 'home':
        params['nodeId'] = house_id
    return {'contractVersion': '1.0', 'requestId': str(uuid.uuid4()), 'locale': 'zh-CN',
            'utterance': text, 'intent': 'light.power.set', 'parameters': params,
            'options': {'dryRun': dry_run}}


class YeelightClient:
    def __init__(self, house_id, executable='yeelight-home', profile=None, dry_run=False, checkpoint=None):
        self.checkpoint = checkpoint
        self.house_id, self.executable = house_id, executable
        self.profile, self.dry_run = profile, dry_run

    def execute(self, text, intent, before_execute=None):
        request = build_request(text, intent, self.house_id, self.dry_run)
        args = [self.executable, 'invoke', '--stdin']
        if self.profile:
            args.extend(['--profile', self.profile])
        if before_execute is not None and not self.dry_run:
            before_execute(intent)
        if self.checkpoint is not None:
            self.checkpoint()
        try:
            result = subprocess.run(args, input=json.dumps(request), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45)
            response = json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise LightError('灯控请求超时，执行结果尚未确认；请检查灯具状态。') from None
        except (OSError, ValueError):
            raise LightError('灯控程序或反馈异常，执行结果尚未确认。') from None
        if (not isinstance(response, dict) or response.get('contractVersion') != '1.0'
                or response.get('requestId') != request['requestId']
                or not isinstance(response.get('status'), str)):
            raise LightError('灯控反馈不符合契约，执行结果尚未确认。')
        # 不依据退出码判断灯具已成功。状态词及读回证据尚待真实设备验收。
        return response


def reply_for(response, dry_run=False):
    if response['status'] == 'auth_required':
        return '易来尚未登录，请先在灯控运行机器上完成登录。'
    if dry_run:
        return '本次是预演，没有执行灯控。'
    status = response['status']
    if status == 'clarification_required':
        return '没有找到唯一的灯光目标，请补充房间和灯具名称。'
    if status == 'partial':
        return '灯控请求已发送，但状态读回不一致，请检查灯具。'
    if status in {'error', 'failed', 'not_supported', 'validation_error'}:
        return '本次灯控未能确认成功，请检查设备连接和目标名称。'
    result = response.get('result', {})
    if status == 'success' and result.get('verified') is True:
        expected, actual = result.get('expectedValue'), result.get('verifiedValue')
        def power(value):
            if type(value) is bool:
                return value
            if type(value) in (int, float) and value in (0, 1):
                return bool(value)
            if isinstance(value, str) and value.lower() in {'on', 'off', 'true', 'false', '0', '1'}:
                return value.lower() in {'on', 'true', '1'}
            return None
        expected, actual = power(expected), power(actual)
        if expected is not None and actual == expected:
            name = result.get('entity', {}).get('name', '灯光')
            if not isinstance(name, str) or not 1 <= len(name) <= 50 or any(ord(c) < 32 for c in name):
                name = '灯光'
            return name + ('已打开。' if actual else '已关闭。')
    return '灯控请求已发送，但灯的实际状态尚未确认，请检查灯具。'
