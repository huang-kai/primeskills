"""灯控状态解析、安全错误与用户反馈。"""

class LightError(Exception):
    pass


def parse_power_state(value):
    if type(value) is bool:
        return value
    if type(value) in (int, float) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {'on', 'off', 'true', 'false', '0', '1'}:
        return value.lower() in {'on', 'true', '1'}
    return None


def reply_for(response):
    if response['status'] == 'auth_required':
        return '易来尚未登录，请先在灯控运行机器上完成登录。'
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
        expected, actual = parse_power_state(expected), parse_power_state(actual)
        if expected is not None and actual == expected:
            name = result.get('entity', {}).get('name', '灯光')
            if not isinstance(name, str) or not 1 <= len(name) <= 50 or any(ord(c) < 32 for c in name):
                name = '灯光'
            return name + ('已打开。' if actual else '已关闭。')
    return '灯控请求已发送，但灯的实际状态尚未确认，请检查灯具。'
