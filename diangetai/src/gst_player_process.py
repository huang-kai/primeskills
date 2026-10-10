"""在SoC0运行的单曲播放器；只从stdin接收URL，日志不输出资源地址。"""
import json
import math
import os
import select
import signal
import sys
import time


def main():
    os.environ['GST_DEBUG'] = '0'
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    data = json.loads(sys.stdin.readline())
    expected = float(data['seconds'])
    if not math.isfinite(expected) or not 0 < expected <= 600:
        raise ValueError('invalid duration')
    limit = data.get('play_seconds', 0)
    if type(limit) is not int or not 0 <= limit <= 600:
        raise ValueError('invalid playback limit')
    pipeline = Gst.parse_launch('uridecodebin name=source ! audioconvert ! audioresample ! pulsesink name=speaker')
    pipeline.get_by_name('source').set_property('uri', data['url'])
    speaker = pipeline.get_by_name('speaker')
    speaker.set_property('client-name', 'qqmusic-stream')
    speaker.set_property('stream-properties', Gst.Structure.new_from_string('props,media.role=(string)music'))
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    def emit(event, **fields):
        print(json.dumps(dict(event=event, **fields)), flush=True)
    bus = pipeline.get_bus()
    ready = False
    playing = False
    started = None
    deadline = time.monotonic() + 40
    try:
        # PAUSED预缓冲不出声；先核对资源总时长，避免播报歌名后才发现是试听。
        if pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('preroll failed')
        while not stopped:
            msg = bus.timed_pop_filtered(50 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg and msg.type == Gst.MessageType.ERROR:
                raise RuntimeError('stream error')
            # 播放板独立执行时限，即使业务板正在等待动作响应，也会准时停歌。
            if limit and started is not None and time.monotonic() - started >= limit:
                emit('ended', reason='limit')
                break
            if msg and msg.type == Gst.MessageType.EOS:
                if started is None or time.monotonic() - started < expected - max(3, expected * .03):
                    raise RuntimeError('premature end')
                emit('ended', reason='natural')
                break
            if not ready:
                _, state, _ = pipeline.get_state(0)
                ok, duration = pipeline.query_duration(Gst.Format.TIME)
                if state == Gst.State.PAUSED and ok:
                    actual = duration / Gst.SECOND
                    if abs(actual - expected) > max(3, expected * .03):
                        raise RuntimeError('resource duration mismatch')
                    ready = True
                    deadline = time.monotonic() + 90
                    emit('ready', seconds=actual)
            if select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if not line or line.strip() == 'stop':
                    break
                if line.strip() != 'play' or not ready or playing:
                    raise RuntimeError('invalid command')
                play_requested = time.monotonic()
                pipeline.set_state(Gst.State.PLAYING)
                emit('timing', stage='歌曲流状态切换', seconds=round(time.monotonic() - play_requested, 3))
                playing = True
                deadline = time.monotonic() + 12
            if playing and started is None:
                ok, position = pipeline.query_position(Gst.Format.TIME)
                if ok and position > 0:
                    started = time.monotonic()
                    deadline = started + expected + 12
                    emit('timing', stage='歌曲流等待开始', seconds=round(started - play_requested, 3))
                    emit('started')
            if time.monotonic() > deadline:
                raise RuntimeError('stream timeout')
    except Exception:
        # Gst错误可能含签名URL，不输出异常正文或debug信息。
        emit('error')
    finally:
        pipeline.set_state(Gst.State.NULL)
        _, state, _ = pipeline.get_state(5 * Gst.SECOND)
        emit('closed', confirmed=state == Gst.State.NULL)


if __name__ == '__main__':
    main()
