"""点歌业务：选曲、定时播放与机器人联动，不管理框架语音会话。"""
import logging
import asyncio
import time

from primebuddy.voice.audio import AudioError
from .configuration.playback import playback_seconds, effects_enabled

logger = logging.getLogger('primebuddy.skills.diangetai.skill')


def validate(action, args):
    if not (args['artist'].strip() or args['song'].strip()):
        raise ValueError('请提供歌手或歌名')


def preflight(action, args, context):
    config = context.config_for('diangetai')
    playback_seconds(config)
    effects_enabled(config)
    if not context.simulate and (context.runtime is None or context.audio is None):
        raise ValueError('点歌需要框架提供音频服务，请同时更新框架和技能并运行 primebuddy start。')


def planning_context(context):
    config = context.config_for('diangetai')
    return {'status': 'success', 'play_seconds': playback_seconds(config),
            'music_effects': effects_enabled(config), 'posture': '四足趴卧先起身；已站立则保持双足或四足形态'}


def describe(action, args):
    return '正在准备播放' + (args['artist'] + '的' if args['artist'] else '') + (args['song'] or '歌曲')


def play(context, artist, song):
    args = {'artist': artist, 'song': song}
    validate('play', args)
    config = context.config_for('diangetai')
    limit, enabled = playback_seconds(config), effects_enabled(config)
    if context.simulate:
        for _ in range(15):
            context.checkpoint()
            time.sleep(.02)
        return {'status': 'success', 'message': '模拟：歌曲播放完毕，播放器已清理。'}
    from .music.qqmusic_client import select_song, load_credential, SkillError
    from .effects.session import effects_session
    if load_credential() is None:
        return {'status': 'auth_required', 'message': '请先完成 QQ 音乐登录。'}
    async def select():
        return await asyncio.wait_for(select_song(args), timeout=60)
    stage = '选歌与取流'
    try:
        song, url = asyncio.run(select())
        context.checkpoint()
        stage = '联动预检与播放' if enabled else '歌曲播放'
        with effects_session(context, enabled) as effects:
            message = play_song(context, song, url, limit, effects)
    except (AudioError, SkillError) as error:
        # 选歌也在安全错误边界内；搜索无结果不能被误报成未知执行失败。
        logger.error('DIANGETAI_ERROR %s %s', stage, type(error).__name__)
        return {'status': 'failed', 'message': str(error)}
    except TimeoutError:
        return {'status': 'failed', 'message': stage + '超时，请检查网络后重试。'}
    except Exception as error:
        # 第三方异常可能含签名 URL，只记本地阶段和类型，正文仍交框架隐藏。
        logger.error('DIANGETAI_ERROR %s %s', stage, type(error).__name__)
        raise
    return {'status': 'success', 'message': message}


def play_song(context, song, url, limit, effects):
    def before_play():
        if effects:
            effects.robot.spin(0)
            effects.prepare_stand()
            context.checkpoint()
            effects.require_stand()

    def tick():
        if effects:
            effects.robot.spin(0)
            effects.tick()

    # 选曲与联动属于技能；解码、计时、原厂播放与清理全部由框架完成。
    result = context.audio.play(url, max_seconds=limit, expected_seconds=song.interval,
                                before_play=before_play,
                                on_started=effects.begin_music if effects else None,
                                on_tick=tick if effects else None)
    return f'已播放 {limit} 秒，歌曲已停止。' if result['limited'] else '歌曲播放完毕。'
