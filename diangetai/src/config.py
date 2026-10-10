"""点歌台自己的配置校验；0 表示整首播放，正数从实际开播起计时。"""
from primebuddy.settings import SetupError


def playback_seconds(config):
    value = config.get('play_seconds', 0)
    if type(value) is not int or not 0 <= value <= 600:
        raise SetupError('点歌台播放时长须为 0～600 的整数秒；0 表示整首。请运行 primebuddy skill configure diangetai。')
    return value


def effects_enabled(config):
    value = config.get('music_effects', False)
    if type(value) is not bool:
        raise SetupError('点歌台联动开关无效，请运行 primebuddy skill configure diangetai。')
    return value


def configure_playback(ui, config, default_seconds):
    current = dict(config)
    while True:
        value = ui.ask('每首播放秒数（0 为整首，最长 600 秒）', str(config.get('play_seconds', default_seconds)))
        try:
            current['play_seconds'] = int(value)
            playback_seconds(current)
        except (ValueError, TypeError):
            ui.info('请输入 0～600 的整数；0 表示整首播放。')
            continue
        return current
