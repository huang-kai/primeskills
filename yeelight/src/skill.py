"""灯控业务，保留现有设备解析与实际状态校验。"""
import time
from .configuration import DEFAULT_EXECUTABLE, SkillError
from .integrations import mcp_client
from .business.feedback import parse_power_state
from .business.intent import IntentError, validate as validate_intent
import re
def validate(args):
    try:
        validate_intent(dict(args, intent='set_power'))
    except IntentError as error:
        raise SkillError(str(error)) from None


def check_power(power, scope, room, device, *, config, simulate=False):
    validate(dict(power=power, scope=scope, room=room, device=device))
    if not simulate and not config.get('house_id', ''):
        raise SkillError('未配置易来家庭 ID')
    return {'status': 'success', 'message': '灯控参数和配置已校验，未执行开关。'}


def context(config, check=lambda: None):
    """只读会话快照，为模型提供真实名称；执行仍重新解析目标。"""
    if not config.get('house_id', ''):
        return {'status': 'failed', 'message': '未配置易来家庭 ID。'}
    with mcp_client.LocalMcp(config.get('executable', DEFAULT_EXECUTABLE), config.get('profile'), check) as mcp:
        response = mcp.call('yeelight_home_list_entities',
                            {'houseId': config.get('house_id', ''), 'locale': 'zh-CN'})
    if response['status'] != 'success':
        return {'status': response['status'], 'message': '易来目标清单读取失败。'}
    entities = response.get('result', {}).get('entities', [])
    def valid(entity):
        name = entity.get('name')
        return isinstance(name, str) and 0 < len(name) <= 80 and not any(ord(c) < 32 for c in name)
    entities = [e for e in entities if isinstance(e, dict) and valid(e)]
    rooms = {e.get('id'): e['name'] for e in entities if e.get('type') == 'room'}
    return {'status': 'success', 'vocabulary': [e['name'] for e in entities][:300],
            'rooms': list(rooms.values())[:100],
            'devices': [{'name': e['name'], 'room': rooms.get(e.get('roomId'), '')}
                        for e in entities if e.get('type') == 'device'][:200]}


def confirmation(args, text, snapshot):
    normalized = lambda value: re.sub(r'[\s，,。！？!?、的]', '', value)
    if args['scope'] == 'home':
        return None if any(word in text for word in ('全屋', '所有灯', '全部灯')) else '全屋范围需确认'
    name = args['device'] if args['scope'] == 'device' else args['room']
    if snapshot.get('status') == 'success':
        candidates = (snapshot.get('devices', []) if args['scope'] == 'device'
                      else [{'name': room} for room in snapshot.get('rooms', [])])
        matches = [c for c in candidates if c['name'] == name
                   and (not args['room'] or args['scope'] != 'device' or c.get('room') == args['room'])]
        if len(matches) != 1:
            raise SkillError('目标不唯一或不在真实清单中，请补充房间和灯名')
    if normalized(name) not in normalized(text):
        return '模型提出了名称修正，需确认'
    if args['scope'] == 'device' and args['room'] and normalized(args['room']) not in normalized(text):
        return '模型补充了房间，需确认'
    return None


def preview_power(power, scope, room, device, text, *, config, check=lambda: None, simulate=False):
    check_power(power, scope, room, device, config=config, simulate=simulate)
    snapshot = {'status': 'unavailable'} if simulate else context(config, check)
    if not simulate and snapshot['status'] != 'success':
        return dict(snapshot, question=None)
    args = dict(power=power, scope=scope, room=room, device=device)
    description = describe(args)
    question = confirmation(args, text, snapshot)
    if question:
        question = description[2:] + '（' + question + '）'
    return {'status': 'success', 'message': '灯控目标已预览，未执行开关。',
            'question': question, 'description': description}


def describe(args):
    target = args['device'] if args['scope'] == 'device' else args['room'] + '灯'
    if args['scope'] == 'home':
        target = '全屋灯'
    elif args['scope'] == 'device' and args['room'] and not target.startswith(args['room']):
        target = args['room'] + '的' + target
    return '正在' + ('打开' if args['power'] else '关闭') + target


def confirm_delayed_state(args, config, check):
    """云端写后可能仍读到旧状态；只复核单灯，不重发写入。"""
    from .integrations.mcp_client import LocalMcp
    query = {'houseId': config.get('house_id', ''), 'deviceName': args['device'], 'locale': 'zh-CN'}
    if args['room']:
        query['roomName'] = args['room']
    with LocalMcp(config.get('executable', DEFAULT_EXECUTABLE), config.get('profile'), check) as mcp:
        for attempt in range(3):
            if attempt:
                # 可取消等待；不阻塞主线程的音频处理。
                for _ in range(20):
                    check()
                    time.sleep(.1)
            check()
            response = mcp.call('yeelight_home_get_state', query)
            if response['status'] != 'success':
                return False
            properties = response.get('result', {}).get('properties', {})
            if (properties.get('online') is True
                    and parse_power_state(properties.get('power')) is args['power']):
                return True
    return False


def set_power(power, scope, room, device, *, config, check=lambda: None, simulate=False):
    args = {'power': power, 'scope': scope, 'room': room, 'device': device}
    check_power(power, scope, room, device, config=config, simulate=simulate)
    check()
    if simulate:
        return {'status': 'success', 'message': '模拟：' + describe(args)[2:] + '完成。'}
    from .integrations.mcp_client import McpLightClient
    from .business.feedback import reply_for
    client = McpLightClient(config.get('house_id', ''), config.get('executable', DEFAULT_EXECUTABLE), config.get('profile'), check)
    response = client.execute(dict(args, intent='set_power'))
    if response.get('suggestedIntent'):
        return {'status': 'clarification_required',
                'message': '灯名未匹配。请重新说完整指令，例如：关闭' + response['suggestedIntent']['device'] + '。'}
    status = response['status']
    immediate_verified = False
    if status == 'success':
        result = response.get('result', {})
        # 只有确认状态一致才允许依赖任务继续。
        expected = args['power']
        immediate_verified = (result.get('verified') is True
                              and parse_power_state(result.get('verifiedValue')) is expected
                              and parse_power_state(result.get('expectedValue')) is expected)
    if status in {'success', 'partial'} and not immediate_verified:
        if args['scope'] == 'device' and confirm_delayed_state(args, config, check):
            return {'status': 'success', 'message': args['device'] + ('已打开。' if args['power'] else '已关闭。')}
        return {'status': 'failed', 'message': '灯控请求已发送，但目标状态尚未确认，请检查灯具。'}
    if status not in {'success', 'auth_required', 'clarification_required'}:
        status = 'failed'
    return {'status': status, 'message': reply_for(response)}
