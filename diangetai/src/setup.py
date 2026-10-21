"""点歌台安装引导：复用有效账号，或扫码登录后向服务器确认。"""
import asyncio

from .music.qqmusic_client import (
    account_lock, check_login, _check_login, device_path, save_credential,
    SkillError, SkillLoginBlockedError, login_error_message,
)
from .configuration.playback import configure_playback, DEFAULTS
from .configuration.qr import show_qr


async def login(ui, data):
    from qqmusic_api import Client
    from qqmusic_api.models.login import QRLoginType
    from qqmusic_api.modules.login_utils import QRCodeLoginSession
    async with account_lock(data):
        # 配置已尝试续期；这里只核实等待期间是否已有其他任务完成登录。
        if await _check_login(data, refresh=False):
            return
        async with Client(device_path=device_path(data)) as client:
            session = QRCodeLoginSession(client.login, QRLoginType.QQ, timeout_seconds=180)
            qr = await asyncio.wait_for(session.get_qrcode(), 30)
            image = qr.save(data)
            if not image:
                raise ValueError('二维码为空')
            with show_qr(ui, image):
                credential = await asyncio.wait_for(session.wait_qrcode_login(), 185)
                # 收到凭据不等于服务已可用，确认成功后才替换原来的登录态。
                async with Client(credential=credential, device_path=device_path(data)) as authenticated:
                    if await asyncio.wait_for(authenticated.login.check_expired(), 30):
                        raise ValueError('登录校验失败')
                save_credential(credential, data)


def setup(ui, data, config):
    ui.info('点歌台 · QQ 音乐账号配置')
    while True:
        try:
            if asyncio.run(check_login(data)):
                ui.info('QQ 音乐账号有效，无需重新扫码。')
                break
            if not ui.confirm('现在扫码登录 QQ 音乐？', default=True):
                return None
            asyncio.run(login(ui, data))
            ui.info('QQ 音乐登录已验证。')
            break
        except SkillLoginBlockedError as error:
            ui.info(str(error))
            return None
        except Exception as error:
            code = getattr(error, 'code', None)
            if code in (20279, 104604, 20277, 20278, 20450):
                ui.info(login_error_message(code))
                return None
            ui.info(str(error) if isinstance(error, SkillError) else
                    '登录未完成（' + type(error).__name__ + '）。请检查网络或手机上的授权状态。')
            if ui.choose('下一步', ['重试登录检查', '稍后配置']) == 1:
                return None
    result = configure_playback(ui, config, default_seconds=DEFAULTS['setup']['play_seconds'])
    ui.info('联动自动识别设备：T1 保持双足/四足形态，四足趴卧可准备起身；Q1 仅接受稳定走跑模式，不进行形态切换。')
    ui.info('启用后点歌会产生身体运动，请确保周围空旷且可随时急停；配置过程不执行动作。')
    result['music_effects'] = ui.confirm('启用音乐联动？', default=config.get('music_effects', DEFAULTS['setup']['music_effects']))
    ui.info('点歌台配置完成。')
    return result
