"""点歌台安装引导：复用有效账号，或扫码登录后向服务器确认。"""
import asyncio

from .qqmusic_client import check_login, save_credential
from .config import configure_playback
from .qr import show_qr


async def login(ui, data):
    from qqmusic_api import Client
    from qqmusic_api.models.login import QRLoginType
    from qqmusic_api.modules.login_utils import QRCodeLoginSession
    async with Client() as client:
        session = QRCodeLoginSession(client.login, QRLoginType.QQ, timeout_seconds=180)
        qr = await asyncio.wait_for(session.get_qrcode(), 30)
        image = qr.save(data)
        if not image:
            raise ValueError('二维码为空')
        with show_qr(ui, image):
            credential = await asyncio.wait_for(session.wait_qrcode_login(), 185)
            # 收到凭据不等于服务已可用，确认成功后才替换原来的登录态。
            async with Client(credential=credential) as authenticated:
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
        except Exception as error:
            ui.info('登录未完成（' + type(error).__name__ + '）。请检查网络或手机上的授权状态。')
            if ui.choose('下一步', ['重新获取二维码 / 重试', '稍后配置']) == 1:
                return None
    result = configure_playback(ui, config, default_seconds=60)
    ui.info('音乐联动会在四足趴卧时先起身到四足站立；已经站立则保持双足/四足形态，再联动舞蹈、表情和灯光。')
    ui.info('启用后点歌会产生身体运动，请确保周围空旷且可随时急停；配置过程不执行动作。')
    result['music_effects'] = ui.confirm('启用音乐联动？', default=config.get('music_effects', True))
    ui.info('点歌台配置完成。')
    return result
