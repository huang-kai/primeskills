"""T1 两种形态使用不同命名动作；Q1 保留独立预设动作控制器。"""
T1_PROFILES = {
    'biped': {'form': 2, 'standing': {201, 300}, 'return_action': 300,
              'dance_action': 208, 'motion': 'BIPED_MIMIC_POPPING', 'label': '双足'},
    'quad': {'form': 1, 'standing': {101, 102}, 'dance_action': 119,
             'motion': 'QUAD_MIMIC', 'label': '四足'},
}


def validate_t1_resource(item, name):
    if item is None or item.intr_info.intr_type.value != 1:
        raise ValueError('未取得可自由打断的 ' + name + ' 资源。')
    kind = item.type
    if type(kind.value) is not int or not (
            getattr(kind, 'MIMIC', None) == 2 and kind.value == 2
            or name in {p['motion'] for p in T1_PROFILES.values()}
            and getattr(kind, 'NONE', None) == 0 and kind.value == 0):
        raise ValueError('T1 舞蹈资源类型不匹配，未发送动作。')
