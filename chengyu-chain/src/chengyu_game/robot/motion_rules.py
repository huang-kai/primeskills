"""只读当前 MC 转换表；仅选择四足开心及返回所需的四个模式。"""
import json
import shlex
import subprocess

from ..support import AudioError
from .device import host_command


NAMES = {101: 'QUADRUPED_STAND_DEFAULT', 102: 'QUADRUPED_LOCOMOTION_DEFAULT',
         121: 'QUADRUPED_LOCOMOTION_STEP', 114: 'QUADRUPED_LOCOMOTION_PLEASURE'}

# 在运控所在板读取实际进程选择的配置，不以 SDK 示例表代替当前固件。
# 只输出这四个模式之间的边，不输出环境、完整配置或其他动作资源。
READ_RULES = '''
import json, pathlib, re, yaml
names = ''' + repr(NAMES) + '''
configs = []
for proc in pathlib.Path('/proc').glob('[0-9]*'):
    try:
        if str((proc / 'exe').resolve()) not in ('/robot/software/mc/bin/mc_app_main', '/robot/software/mc/bin/mc_main'):
            continue
        values = [v.split(b'=', 1)[1].decode() for v in (proc / 'environ').read_bytes().split(b'\\0')
                  if v.startswith(b'ROBOT_CFG=')]
        configs.extend(values)
    except (OSError, UnicodeError):
        continue
if len(configs) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]+', configs[0]):
    raise RuntimeError('MC config selection is not unique')
path = pathlib.Path('/robot/software/mc/bin/cfg/robot') / configs[0] / 'action_ruler.yaml'
rules = yaml.safe_load(path.read_text())['action_rules']
edges = {str(source): [target for target, name in names.items()
                      if name in rules[names[source]]['next_actions']] for source in names}
print(json.dumps(edges))
'''


def select_quad_route(edges, initial):
    if type(initial) is not int or initial not in (101, 102):
        raise AudioError('四足开心只允许从已确认的四足站立开始。')
    def path(start, target):
        if target in edges.get(str(start), []):
            return [target]
        if 121 in edges.get(str(start), []) and target in edges.get('121', []):
            return [121, target]
        raise AudioError('当前固件没有已核对的四足开心或返回路径。')
    route = {'enter': path(initial, 114), 'return': path(114, initial)}
    if 121 in route['enter'] + route['return'] and initial not in edges.get('121', []):
        raise AudioError('当前固件未提供中间模式的取消返回路径。')
    if initial != 102 and route['return'][0] not in edges.get('102', []):
        raise AudioError('当前固件未提供开心自然结束到102后的归位路径。')
    return route


def valid_quad_route(route, initial):
    return (type(initial) is int and initial in (101, 102) and isinstance(route, dict)
            and set(route) == {'enter', 'return'}
            and route['enter'] in ([114], [121, 114])
            and route['return'] in ([initial], [121, initial])
            and all(type(value) is int for values in route.values() for value in values))


def load_quad_route(runtime, initial):
    try:
        result = subprocess.run(host_command(runtime, 'python3 -c ' + shlex.quote(READ_RULES)),
                                capture_output=True, timeout=10, check=True)
        if len(result.stdout) > 4096:
            raise ValueError()
        edges = json.loads(result.stdout)
        if (not isinstance(edges, dict) or set(edges) != {str(value) for value in NAMES}
                or any(not isinstance(items, list) or any(type(item) is not int or item not in NAMES
                                                         for item in items) for items in edges.values())):
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError):
        raise AudioError('未能读取当前 MC 的四足转换规则，未发送开心动作。') from None
    return select_quad_route(edges, initial)
