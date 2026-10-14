"""灯控参数校验，不包含 LLM 路由。"""
class IntentError(Exception):
    pass


def validate(value):
    if not isinstance(value, dict) or set(value) != {'intent', 'power', 'scope', 'room', 'device'}:
        raise IntentError('意图结构无效，未控制灯光。')
    if not isinstance(value['intent'], str) or value['intent'] not in {'set_power', 'clarify', 'cancel', 'exit_skill', 'non_light'}:
        raise IntentError('未知灯控意图。')
    if value['power'] is not None and type(value['power']) is not bool:
        raise IntentError('开关字段须为布尔值。')
    for key in ('scope', 'room', 'device'):
        if not isinstance(value[key], str) or len(value[key]) > 80 or any(ord(c) < 32 for c in value[key]):
            raise IntentError('灯控目标字段无效。')
    if value['scope'] not in {'', 'device', 'room', 'home'}:
        raise IntentError('灯控范围无效。')
    value = dict(value, **{key: value[key].strip() for key in ('scope', 'room', 'device')})
    if value['intent'] == 'set_power':
        scope = value['scope']
        if (type(value['power']) is not bool or not scope
                or scope == 'device' and not value['device']
                or scope == 'room' and (not value['room'] or value['device'])
                or scope == 'home' and (value['room'] or value['device'])):
            raise IntentError('灯控目标缺失或矛盾。')
    if value['intent'] in {'cancel', 'exit_skill', 'non_light'} and (
            value['power'] is not None or any(value[k] for k in ('scope', 'room', 'device'))):
        raise IntentError('非执行意图含有灯控参数。')
    return value
