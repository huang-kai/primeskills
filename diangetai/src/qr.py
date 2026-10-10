"""QQ 登录二维码的展示和清理，使用框架的通用消息输出保持界面一致。"""
from contextlib import contextmanager
import shutil


@contextmanager
def show_qr(ui, path):
    """显示服务端生成的原始二维码；终端渲染失败时仍保留图片供查看。"""
    try:
        path.chmod(0o600)
        ui.info('请用手机扫描二维码并确认登录。Ctrl+C 可取消。')
        try:
            render_qr(ui, path)
        except (ImportError, OSError, ValueError):
            ui.info('终端无法显示二维码，请打开此私有图片：' + str(path))
        yield
    finally:
        path.unlink(missing_ok=True)


def render_qr(ui, path):
    # Pillow 由点歌台自己声明和加载，框架不参与二维码处理。
    from PIL import Image
    with Image.open(path) as source:
        if source.width > 2048 or source.height > 2048:
            raise ValueError('二维码图片过大')
        image = source.convert('L').point(lambda x: 255 if x < 128 else 0)
        box = image.getbbox()
        if box is None:
            raise ValueError('二维码为空')
        image = image.crop(box)
        # 左上定位块顶边恰好为七个模块，只接受整数像素的原始二维码。
        run = 0
        while run < image.width and image.getpixel((run, 0)):
            run += 1
        scale = run // 7
        if not scale or run % 7 or image.width != image.height or image.width % scale:
            raise ValueError('二维码像素不规则')
        count = image.width // scale
        if not 21 <= count <= 177 or (count - 21) % 4:
            raise ValueError('二维码尺寸无效')
        if (count + 8) * 2 > shutil.get_terminal_size((120, 40)).columns:
            raise ValueError('终端过窄')
        small = image.resize((count, count), Image.Resampling.NEAREST)
        # 校验缩放无损，避免生成看似正确但扫不出的二维码。
        if small.resize(image.size, Image.Resampling.NEAREST).tobytes() != image.tobytes():
            raise ValueError('二维码不是整齐的像素网格')
        for y in range(-4, count + 4):
            row = ''
            for x in range(-4, count + 4):
                black = 0 <= x < count and 0 <= y < count and small.getpixel((x, y))
                row += ('\033[40m' if black else '\033[107m') + '  '
            ui.info(row + '\033[0m')
        ui.info('若终端显示异常，可打开原始图片：' + str(path))
