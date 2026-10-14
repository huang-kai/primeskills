"""易来技能自己的配置与只读连接验证；框架无需了解家庭或 MCP 字段。"""
import shutil
from .configuration import DEFAULT_EXECUTABLE

from .integrations.mcp_client import LocalMcp


def setup(ui, data, config):
    ui.info('易来灯控 · 请先完成本地 yeelight-home 工具的账号登录。')
    while True:
        if not ui.confirm('现在配置易来连接？', default=True):
            return None
        executable = ui.ask('yeelight-home 可执行文件', config.get('executable', DEFAULT_EXECUTABLE))
        house = ui.ask('家庭 ID', config.get('house_id', ''))
        profile = ui.ask('配置档名称（default 使用工具默认账号）', config.get('profile') or 'default')
        current = {'executable': executable, 'house_id': house, 'profile': None if profile == 'default' else profile}
        if not shutil.which(executable):
            ui.info('找不到可执行文件，请先安装易来工具或填写绝对路径。')
        else:
            try:
                with LocalMcp(executable, current['profile']) as mcp:
                    result = mcp.call('yeelight_home_list_entities', {'houseId': house, 'locale': 'zh-CN'})
                if result['status'] == 'success':
                    ui.info('家庭读取成功，易来技能配置完成。')
                    return current
                ui.info('连接尚未就绪，请检查账号授权和家庭 ID。')
            except Exception as error:
                ui.info('连接检查失败（' + type(error).__name__ + '），请检查易来工具和账号。')
        config = current
        if ui.choose('下一步', ['修改并重试', '稍后配置']) == 1:
            return None
