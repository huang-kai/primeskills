"""灯控业务，保留现有设备解析与实际状态校验。"""
import time
from .configuration import DEFAULT_EXECUTABLE
from .integrations import mcp_client
from .business.feedback import parse_power_state
from primebuddy.core.grounding import normalized


def validate(action, args):
    scope = args['scope']
    if scope == 'device' and not args['device'].strip():
        raise ValueError('缺少灯具名称')
    if scope == 'room' and (not args['room'].strip() or args['device']):
        raise ValueError('房间灯控参数矛盾')
    if scope == 'home' and (args['room'] or args['device']):
        raise ValueError('全屋灯控不能附带局部目标')


def preflight(action, args, context):
    if not context.simulate and not context.config_for('yeelight').get('house_id', ''):
        raise ValueError('未配置易来家庭 ID')


def planning_context(context):
    """只读会话快照，为模型提供真实名称；执行仍重新解析目标。"""
    if context.simulate or not context.config_for('yeelight').get('house_id', ''):
        return {'status': 'unavailable'}
    with mcp_client.LocalMcp(context.config_for('yeelight').get('executable', DEFAULT_EXECUTABLE), context.config_for('yeelight').get('profile'), context.checkpoint) as mcp:
        response = mcp.call('yeelight_home_list_entities',
                            {'houseId': context.config_for('yeelight').get('house_id', ''), 'locale': 'zh-CN'})
    if response['status'] != 'success':
        return {'status': response['status']}
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


def describe(action, args):
    target = args['device'] if args['scope'] == 'device' else args['room'] + '灯'
    if args['scope'] == 'home':
        target = '全屋灯'
    elif args['scope'] == 'device' and args['room'] and not target.startswith(args['room']):
        target = args['room'] + '的' + target
    return '正在' + ('打开' if args['power'] else '关闭') + target


def confirmation(action, args, text, snapshot):
    if args['scope'] == 'home':
        return None if any(word in text for word in ('全屋', '所有灯', '全部灯')) else '全屋范围需确认'
    name = args['device'] if args['scope'] == 'device' else args['room']
    if snapshot.get('status') == 'success':
        candidates = (snapshot.get('devices', []) if args['scope'] == 'device'
                      else [{'name': room} for room in snapshot.get('rooms', [])])
        matches = [c for c in candidates if c['name'] == name
                   and (not args['room'] or args['scope'] != 'device' or c.get('room') == args['room'])]
        if len(matches) != 1:
            raise ValueError('目标不唯一或不在真实清单中，请补充房间和灯名')
    if normalized(name) not in normalized(text):
        return '模型提出了名称修正，需确认'
    if args['scope'] == 'device' and args['room'] and normalized(args['room']) not in normalized(text):
        return '模型补充了房间，需确认'
    return None


def confirm_delayed_state(args, context):
    """云端写后可能仍读到旧状态；只复核单灯，不重发写入。"""
    from .integrations.mcp_client import LocalMcp
    query = {'houseId': context.config_for('yeelight').get('house_id', ''), 'deviceName': args['device'], 'locale': 'zh-CN'}
    if args['room']:
        query['roomName'] = args['room']
    with LocalMcp(context.config_for('yeelight').get('executable', DEFAULT_EXECUTABLE), context.config_for('yeelight').get('profile'), context.checkpoint) as mcp:
        for attempt in range(3):
            if attempt:
                # 可取消等待；不阻塞主线程的音频处理。
                for _ in range(20):
                    context.checkpoint()
                    time.sleep(.1)
            context.checkpoint()
            response = mcp.call('yeelight_home_get_state', query)
            if response['status'] != 'success':
                return False
            properties = response.get('result', {}).get('properties', {})
            if (properties.get('online') is True
                    and parse_power_state(properties.get('power')) is args['power']):
                return True
    return False


def set_power(context, power, scope, room, device):
    args = {'power': power, 'scope': scope, 'room': room, 'device': device}
    validate('set_power', args)
    context.checkpoint()
    if context.simulate:
        return {'status': 'success', 'message': '模拟：' + describe('set_power', args)[2:] + '完成。'}
    from .integrations.mcp_client import McpLightClient
    from .business.feedback import reply_for
    client = McpLightClient(context.config_for('yeelight').get('house_id', ''), context.config_for('yeelight').get('executable', DEFAULT_EXECUTABLE), context.config_for('yeelight').get('profile'), context.checkpoint)
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
        if args['scope'] == 'device' and confirm_delayed_state(args, context):
            return {'status': 'success', 'message': args['device'] + ('已打开。' if args['power'] else '已关闭。')}
        return {'status': 'failed', 'message': '灯控请求已发送，但目标状态尚未确认，请检查灯具。'}
    if status not in {'success', 'auth_required', 'clarification_required'}:
        status = 'failed'
    return {'status': status, 'message': reply_for(response)}
