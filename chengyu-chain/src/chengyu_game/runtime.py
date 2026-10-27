"""独立前台应用的资源所有权；恢复未确认时不交还宿主。"""
from contextlib import ExitStack
import os
from pathlib import Path

from .configuration import load_config
from .providers import Models
from .session import Session, synthesize
from .support import AudioError, Cancelled, exclusive, frontend_lock, is_running, measure, progress, read_json, write_json

RECORDS = ('media.json', 'effects.json', 'agent_mode.json', 'session.json')
STAND_NOTICE = '请确认四周安全，我要站起来了。'


def status(runtime):
    try:
        running = is_running(runtime)
        pending = [name for name in RECORDS if (runtime / name).exists()]
        released = not running and not pending
        return {'status': 'success' if released else 'failed', 'resources_released': released,
                'running': running, 'pending': pending, 'recovery_required': bool(pending),
                'message': '游戏资源已释放。' if released else '游戏运行中或存在未确认操作。'}
    except OSError:
        return {'status': 'failed', 'resources_released': False, 'recovery_required': True,
                'message': '无法确认游戏运行目录。'}


def cache_ending(models, data):
    import tempfile
    audio = synthesize(models, '成语接龙结束啦，我们下次再一起玩吧！')
    fd, temporary = tempfile.mkstemp(prefix='.ending-', dir=data)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(audio)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, data / 'ending.wav')
    finally:
        Path(temporary).unlink(missing_ok=True)


def ending_ready(data):
    import wave
    try:
        path = data / 'ending.wav'
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError()
        with wave.open(str(path), 'rb') as source:
            if (source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (1, 2, 'NONE'):
                raise ValueError()
            if source.getframerate() not in (16000, 24000, 48000) or not 1 <= source.getnframes() / source.getframerate() <= 30:
                raise ValueError()
            if len(source.readframes(source.getnframes())) != source.getnframes() * 2:
                raise ValueError()
    except (OSError, ValueError, EOFError, wave.Error):
        raise AudioError('缺少有效离线结束播报，请先运行 prepare-audio。') from None


def check_play(config, data, runtime):
    Models(config)  # 只验证游戏独立配置，不调用云模型。
    ending_ready(data)
    result = status(runtime)
    if not result['resources_released']:
        raise AudioError(result['message'])
    from .robot.device import detect_device
    device = detect_device()
    return {'status': 'success', 'message': '配置、结束播报及运行状态检查通过。',
            'series': device.series, 'sku': device.sku, 'resources_released': True}


def play(config, data, runtime, checkpoint, *, hosted=False, manage_agent=False, motion_enabled=None):
    from .robot.device import detect_device
    from .robot.transport import Robot
    from .robot.native_media import NativeMedia
    from .robot.native_agent import read_agent_mode, voice_only_agent
    from .robot.celebration import Celebration
    from .voice import VoiceInput
    if not hosted and not manage_agent:
        raise AudioError('独立运行须明确使用 --manage-agent，由游戏切换并恢复原厂语音模式。')
    if hosted and manage_agent:
        raise AudioError('宿主模式不能管理原厂 Agent。')
    if motion_enabled is not None and type(motion_enabled) is not bool:
        raise AudioError('动作开关须为布尔值。')
    if motion_enabled is True:
        config = {**config, 'celebration': {**config.get('celebration', {}), 'enabled': True}}
    check_play(config, data, runtime)
    parent = os.getppid()
    if hosted and parent <= 1:
        raise AudioError('未发现有效宿主进程。')
    def check():
        checkpoint()
        if hosted and os.getppid() != parent:
            raise Cancelled('宿主已退出，结束游戏并清理资源。')
    device = detect_device()
    with exclusive(runtime):
        # 锁内复核，避免两个进程都在锁外通过预检。
        if any((runtime / name).exists() for name in RECORDS):
            raise AudioError('存在未完成的游戏记录，请先运行 recover。')
        write_json(runtime / 'session.json', {'hosted': hosted, 'series': device.series,
                                            'sku': device.sku, 'pid': os.getpid()})
        try:
            with ExitStack() as stack:
                if not hosted:
                    stack.enter_context(frontend_lock())
                    stack.enter_context(voice_only_agent(runtime))
                with measure('robot_init'):
                    robot = Robot(runtime, check)
                    stack.callback(robot.close)
                if hosted:
                    with measure('mode_check'):
                        if read_agent_mode(robot) != 'only_voice':
                            raise AudioError('Buddy 尚未准备好原厂 only_voice 前端；游戏未修改 Agent。')
                with measure('frontend_ready'):
                    media = NativeMedia(robot, runtime / 'media.json')
                    stack.callback(media.close)
                    effects = Celebration(robot, runtime, config)
                    stack.callback(effects.close)
                    voice = VoiceInput(robot)
                    stack.callback(voice.close)
                    voice.start()
                    effects.start()
                session = Session(Models(config), robot, voice, media, effects, data / 'ending.wav',
                                  speech=config.get('speech', {}))
                readiness = effects.prepare_for_game(motion_enabled=motion_enabled,
                    before_stand=lambda: session.say(STAND_NOTICE))
                if readiness['status'] == 'unavailable':
                    progress('身体动作暂不可用：' + readiness['reason'])
                    session.start_notice = '动作暂时用不了，我们先玩语音接龙吧。'
                return session.run()
        finally:
            # 无论错误/信号都先释放订阅、自己的媒体和动作，然后才结束前台进程。
            if not any((runtime / name).exists() for name in RECORDS[:-1]):
                (runtime / 'session.json').unlink(missing_ok=True)


def recover(runtime, config_path):
    from .robot.transport import Robot
    from .robot.native_media import NativeMedia
    with exclusive(runtime):
        if (runtime / 'media.json').exists():
            with ExitStack() as stack:
                robot = Robot(runtime, lambda: None)
                stack.callback(robot.close)
                media = NativeMedia(robot, runtime / 'media.json')
                stack.callback(media.close)
                media.recover()
        if (runtime / 'effects.json').exists():
            from .robot.celebration import Celebration
            with ExitStack() as stack:
                robot = Robot(runtime, lambda: None)
                stack.callback(robot.close)
                effects = Celebration(robot, runtime, load_config(config_path))
                stack.callback(effects.close)
                effects.recover()
        if (runtime / 'agent_mode.json').exists():
            # 只有独立模式的私有记录能触发恢复；宿主运行不创建此记录。
            from .robot.native_agent import agent_target, agent_backend, restore_agent
            saved = read_json(runtime / 'agent_mode.json')
            if (saved.get('purpose') != 'chengyu_session' or saved.get('target') != agent_target()
                    or saved.get('previous_running') is not True
                    or saved.get('previous_mode') not in ('normal', 'only_voice')
                    or saved.get('backend') != agent_backend(runtime)):
                raise AudioError('原厂模式恢复记录与当前设备不匹配，未修改。')
            with frontend_lock():
                restore_agent(runtime, saved['previous_mode'], saved['backend'])
        (runtime / 'session.json').unlink(missing_ok=True)
    return status(runtime)
