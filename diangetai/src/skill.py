"""点歌业务：选曲、定时播放与机器人联动，不管理框架语音会话。"""
import asyncio
import time

from primebuddy.voice_support.robot_audio import AudioError
from .config import playback_seconds, effects_enabled


def validate(action, args):
    if not (args['artist'].strip() or args['song'].strip()):
        raise ValueError('请提供歌手或歌名')


def preflight(action, args, context):
    config = context.config_for('diangetai')
    playback_seconds(config)
    effects_enabled(config)
    if not context.simulate and context.runtime is None:
        raise ValueError('点歌需要已连接的机器人音频运行目录')


def planning_context(context):
    config = context.config_for('diangetai')
    return {'status': 'success', 'play_seconds': playback_seconds(config),
            'music_effects': effects_enabled(config), 'posture': '四足趴卧先起身；已站立则保持双足或四足形态'}


def describe(action, args):
    return '正在准备播放' + (args['artist'] + '的' if args['artist'] else '') + (args['song'] or '歌曲')


def run(action, args, context):
    config = context.config_for('diangetai')
    limit, enabled = playback_seconds(config), effects_enabled(config)
    if context.simulate:
        for _ in range(15):
            context.checkpoint()
            time.sleep(.02)
        return {'status': 'success', 'message': '模拟：歌曲播放完毕，播放器已清理。'}
    from .qqmusic_client import select_song, load_credential, SkillError
    from .effects_session import effects_session
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
        print('DIANGETAI_ERROR', stage, type(error).__name__, flush=True)
        return {'status': 'failed', 'message': str(error)}
    except TimeoutError:
        return {'status': 'failed', 'message': stage + '超时，请检查网络后重试。'}
    except Exception as error:
        # 第三方异常可能含签名 URL，只记本地阶段和类型，正文仍交框架隐藏。
        print('DIANGETAI_ERROR', stage, type(error).__name__, flush=True)
        raise
    return {'status': 'success', 'message': message}


def play_song(context, song, url, limit, effects):
    from .stream_audio import RemoteSong
    player = RemoteSong(context.runtime, url, song.interval, play_seconds=limit)
    try:
        player.wait_ready(context.checkpoint)
        context.checkpoint()
        if effects:
            effects.robot.spin(0)
            effects.prepare_stand()
            context.checkpoint()
            effects.require_stand()
        player.send('play')
        requested = time.monotonic()
        while True:
            context.checkpoint()
            player.update()
            if player.error or (player.proc.poll() is not None and not player.ended):
                raise AudioError('歌曲播放器异常退出。')
            if player.ended:
                break
            # 从确认开播起计时，搜索、预缓冲和提示语不占播放时长。
            if limit and player.started is not None and time.monotonic() - player.started >= limit:
                break
            if player.started is None and time.monotonic() - requested > 15:
                raise AudioError('播放开始未确认。')
            if time.monotonic() - requested > song.interval + 60:
                raise AudioError('播放结束超时。')
            if effects:
                effects.robot.spin(0)
                if player.started is not None:
                    if effects.original_light_state is None:
                        effects.begin_music()
                    effects.tick()
            time.sleep(.05)
    finally:
        # 先停本曲，再由 effects_session 恢复；播放器清理失败也要尝试恢复联动。
        player.stop()
    limited = not player.ended or player.end_reason == 'limit'
    return f'已播放 {limit} 秒，歌曲已停止。' if limited else '歌曲播放完毕。'
