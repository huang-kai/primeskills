"""Q1、T1 双足和 T1 四足的资源协议；资源可用不等于已完成实体验收。"""
# 表情编号、运控模式和预设动作使用不同接口，不能互换。
FACES = {'T1': {'happy': 90, 'sad': 21, 'reset': 13},
         'Q1': {'happy': 3003, 'sad': 3018, 'reset': 3001}}
# Q1 难过 3018 已在当前固件 emotions.yaml 核实，旧 SDK 的表情表不完整。
OUTCOMES = {'success': 'happy', 'win': 'happy', 'failure': 'sad', 'lose': 'sad'}
CHARGING_MASK = 1 | (1 << 25) | (1 << 26)
PROFILES = {
    'T1_biped': {'initial': {201, 300}, 'mode': 300, 'preset': 2002, 'form': 2,
                 'enum': 'BOTH_HANDS_ACTION_HAPPY', 'return_mode': 300},
    'T1_quad': {'initial': {101, 102}, 'mode': 114, 'form': 1,
                'enum': 'QUADRUPED_LOCOMOTION_PLEASURE'},
    'Q1': {'initial': {1002}, 'mode': 1005, 'preset': 3005,
           'enum': 'Q_SHOW_HAPPINESS'},
}
SAD_PROFILES = {
    'T1_biped': {'initial': {201, 300}, 'mode': 300, 'preset': 2033, 'form': 2,
                 'enum': 'HUMANOID_SAD', 'optional_enum': True, 'return_mode': 300},
    'T1_quad': {'initial': {101, 102}, 'mode': 119, 'form': 1,
                'enum': 'QUADRUPED_MIMIC', 'motion': 'QUAD_MIMIC_SAD'},
}
ACTION_NAMES = {101: 'QUADRUPED_STAND_DEFAULT', 102: 'QUADRUPED_LOCOMOTION_DEFAULT',
                121: 'QUADRUPED_LOCOMOTION_STEP',
                114: 'QUADRUPED_LOCOMOTION_PLEASURE', 201: 'BIPED_STAND_DEFAULT',
                300: 'BIPED_LOCOMOTION_WBC', 1002: 'BIPED_WALK_RUN',
                1005: 'BIPED_WHOLE_BODY_CTRL'}


def validate_preset_resources(profile_name, profile, resources, preset_type):
    """Q1 的复位是已枚举资源；T1 的 DEFAULT_POS 是控制命令，不要求枚举。"""
    if getattr(preset_type, 'DEFAULT_POS', None) != 0:
        raise ValueError('默认姿态枚举未确认。')
    values = (0, profile['preset']) if profile_name == 'Q1' else (profile['preset'],)
    for value in values:
        item = next((item for item in resources if item.tag == str(value)), None)
        if item is None or item.type.value != 1 or item.intr_info.intr_type.value != 1:
            raise ValueError('预设动作资源缺失或不能自由打断；未发送动作。')


def validate_t1_mimic_resource(resources, name):
    item = next((item for item in resources if item.tag == name), None)
    if item is None or item.intr_info.intr_type.value != 1:
        raise ValueError('四足难过资源缺失或不能自由打断，未发送动作。')
    kind = item.type
    # T1 当前固件对命名 Mimic 返回 NONE；只允许已核对名称，不扩展到未知资源。
    typed = getattr(kind, 'MIMIC', None) == 2 and kind.value == 2
    unspecified = name == 'QUAD_MIMIC_SAD' and getattr(kind, 'NONE', None) == 0 and kind.value == 0
    if type(kind.value) is not int or not (typed or unspecified):
        raise ValueError('四足难过注册资源类型未匹配 MIMIC/NONE，未发送动作。')
