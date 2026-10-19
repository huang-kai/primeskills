"""点歌业务：选曲、定时播放与机器人联动，不管理框架语音会话。"""
import logging
import asyncio
import time
from pathlib import Path

from .support import AudioError, SetupError
from .configuration.playback import playback_seconds, effects_enabled

logger = logging.getLogger('primebuddy.skills.diangetai.skill')


def validate(args):
    if not (args['artist'].strip() or args['song'].strip()):
        raise SetupError('请提供歌手或歌名')


def settings(config):
    return {'status': 'success', 'play_seconds': playback_seconds(config),
            'music_effects': effects_enabled(config), 'posture': '按设备和实时姿态适配；Q1 仅在稳定走跑模式跳舞，T1 保持双足或四足形态'}


def check_play(artist, song, *, config, runtime, simulate=False):
    validate({'artist': artist, 'song': song})
    settings(config)
    if not simulate and runtime is None:
        raise SetupError('点歌需要可写的运行目录。')
    return {'status': 'success', 'message': '点歌参数和播放设置已校验，未执行播放。'}


def play(artist, song, *, config, runtime, check=lambda: None, simulate=False):
    args = {'artist': artist, 'song': song}
    check_play(artist, song, config=config, runtime=runtime, simulate=simulate)
    limit, enabled = playback_seconds(config), effects_enabled(config)
    if simulate:
        for _ in range(15):
            check()
            time.sleep(.02)
        return {'status': 'success', 'message': '模拟：歌曲播放完毕，播放器已清理。'}
    from .music.qqmusic_client import select_song, load_credential, SkillError, SkillNetworkError
    if load_credential() is None:
        return {'status': 'auth_required', 'message': '请先完成 QQ 音乐登录。'}
    async def select():
        task = asyncio.create_task(select_song(args))
        try:
            deadline = time.monotonic() + 60
            while not task.done():
                check()
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                await asyncio.sleep(.1)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    stage = '选歌与取流'
    try:
        song, url = asyncio.run(select())
        check()
        stage = '联动预检与播放' if enabled else '歌曲播放'
        robot = get_robot(runtime)
        with robot.session(check), robot.effects(enabled) as effects:
            message = play_song(check, robot, song, url, limit, effects)
    except (SkillNetworkError, TimeoutError) as error:
        logger.error('DIANGETAI_ERROR %s %s', stage, type(error).__name__)
        if isinstance(error, SkillNetworkError) or stage == '选歌与取流':
            return {'status': 'failed', 'message': '网络连接出现了问题，请重新连接网络。',
                    'error_code': 'network_unavailable'}
        return {'status': 'failed', 'message': stage + '超时，请重试。'}
    except (SetupError, SkillError) as error:
        # 选歌也在安全错误边界内；搜索无结果不能被误报成未知执行失败。
        logger.error('DIANGETAI_ERROR %s %s', stage, type(error).__name__)
        return {'status': 'failed', 'message': str(error)}
    except Exception as error:
        # 第三方异常可能含签名 URL；只记录阶段和类型，由 CLI 隐藏异常正文。
        logger.error('DIANGETAI_ERROR %s %s', stage, type(error).__name__)
        raise
    return {'status': 'success', 'message': message}


def play_song(check, robot, song, url, limit, effects):
    def before_play():
        if effects:
            effects.prepare()
            check()
            effects.ready()

    def tick():
        if effects:
            effects.tick()

    # 独立 skill 自己管理原厂播放，真实 END 与有界清理完成后才向宿主返回。
    result = robot.play_music(url, max_seconds=limit, expected_seconds=song.interval,
                                before_play=before_play,
                                on_started=effects.begin if effects else None,
                                on_tick=tick if effects else None)
    return f'已播放 {limit} 秒，歌曲已停止。' if result['limited'] else '歌曲播放完毕。'


_robot = None


def get_robot(runtime):
    global _robot
    if _robot is None:
        from .robot.facade import RobotFacade
        _robot = RobotFacade(runtime)
    elif _robot.runtime != Path(runtime):
        raise AudioError('点歌运行目录发生变化，请关闭旧会话后重试。')
    return _robot


def shutdown():
    global _robot
    if _robot is not None:
        try:
            _robot.close()
        finally:
            _robot = None
