import json
import os
import socket
import sys
import time
from pathlib import Path
from collections import deque
from preview_encoder import H264_COMMAND, PreviewEncoder
import numpy as np
import cv2
from detector import PersonDetector
from fall_preview import screen_frame
import rclpy
from rclpy.qos import qos_profile_sensor_data
from aimdk_msgs.srv import CaptureJpegImage
from aimdk_msgs.msg import McCommonState
from floor_detection import FallAnnouncement, FloorTest
from posture import StandingView


def log(*values):
    print(*values, flush=True)


def capture_standing_frame(node, client, request, view, started):
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=1.2)
    if not future.done():
        client.remove_pending_request(future)
        return None
    received = time.monotonic()
    while view.last_state <= received and time.monotonic() < received + .35:
        rclpy.spin_once(node, timeout_sec=.03)
    if not view.stable(time.monotonic()) or view.last_state <= received or view.last_bad >= started:
        return None
    result = future.result()
    if (not result or result.response.header.code or result.jpeg.camera_id != request.camera_id
            or not len(result.jpeg.image.data)):
        return None
    return cv2.imdecode(np.frombuffer(result.jpeg.image.data, np.uint8), cv2.IMREAD_REDUCED_COLOR_4)


def main():
    directory = Path(__file__).parent
    target_pose = 'quadruped' if '--quadruped' in sys.argv else 'biped'
    case_once = '--case-once' in sys.argv
    confirm_family = case_once or '--confirm-family' in sys.argv
    continuous = confirm_family or '--manual-test' in sys.argv
    preview_enabled = not (case_once and os.environ.get('T1_FALL_NO_PREVIEW') == '1')
    if continuous:
        # Exactly one detector owns the active event, including across direct and service launches.
        watch_lock = socket.socket(socket.AF_UNIX)
        watch_lock.bind('\0t1-fall-alert-monitor')
    settings = None
    if confirm_family:
        if '--config-stdin' in sys.argv:
            from settings import parse
            settings = parse(sys.stdin.read())
    floor_test = None
    if '--floor-test' in sys.argv or continuous:
        floor_test = FloorTest(settings['detection']['hold_seconds']) if settings else FloorTest()
    badges = None
    if floor_test:
        badges = cv2.imread(str(directory / 'floor_status.png'), cv2.IMREAD_COLOR)
        if badges is None or badges.shape != (84, 142, 3):
            raise RuntimeError('Missing floor test screen labels')

    cv2.setNumThreads(1)
    rclpy.init()
    n = rclpy.create_node('fall_walk_preview_yolo')
    notifier = family = None
    case_started = False
    case_decision = None
    if confirm_family:
        from family_confirmation import FamilyConfirmation
        from sms_sender import RobotNotifier
        notifier = RobotNotifier.from_config(case_once=case_once, settings=settings)
        if case_once:
            def end_case(event, decision):
                nonlocal case_decision
                notifier.result(event, decision)
                case_decision = decision
            on_result = end_case
        else:
            n.create_timer(30.0, notifier.retry_unsubmitted)
            on_result = notifier.result
        family = FamilyConfirmation(n, on_result, settings['voice']['response_seconds']) if settings else FamilyConfirmation(n, on_result)
    if continuous and preview_enabled:
        from std_msgs.msg import Bool
        display = n.create_publisher(Bool, '/t1_fall_alert/preview_enabled', 1)
        def display_heartbeat():
            display.publish(Bool(data=not notifier or not notifier.accepted.is_set()))
        n.create_timer(1.0, display_heartbeat)
    if floor_test:
        announcement = FallAnnouncement(notifier)
    if floor_test and not family:
        from aimdk_msgs.srv import PlayTts
        from uuid import uuid4
        tts = n.create_client(PlayTts, '/aimdk_5Fmsgs/srv/PlayTts')
        if not tts.wait_for_service(timeout_sec=8):
            raise RuntimeError('Fall test voice service unavailable')


    def announce_floor(frame):
        request = PlayTts.Request()
        request.header.header.stamp = n.get_clock().now().to_msg()
        request.tts_req.text = '检测到有人倒在地面上。'
        request.tts_req.domain = 't1-fall-alert'
        request.tts_req.trace_id = 'floor_' + uuid4().hex
        request.tts_req.priority_level.value = request.tts_req.priority_level.INTERACTION_L6
        request.tts_req.priority_weight = 10
        request.tts_req.is_interrupted = False
        def received(future):
            try:
                result = future.result()
                log('VOICE_RESULT', frame, result.header.header.code, result.tts_resp.is_success,
                    result.tts_resp.error_message)
            except Exception as error:
                log('VOICE_RESULT_UNKNOWN', frame, str(error))
        log('VOICE_SUBMIT', frame, request.tts_req.trace_id)
        tts.call_async(request).add_done_callback(received)
    view = StandingView(target_pose, continuous, floor_test)
    n.create_subscription(McCommonState, '/aima/mc/common/state',
                          lambda msg: view.update(msg, time.monotonic()), qos_profile_sensor_data)
    client = n.create_client(CaptureJpegImage, '/aima/hal/camera/CaptureJpegImage')
    if not client.wait_for_service(timeout_sec=6):
        raise RuntimeError('camera unavailable')
    req = CaptureJpegImage.Request()
    req.camera_id = 'head_stereo_left'
    req.timeout_ms = 800

    frames = boxed = attempts = 0
    highest = 0.
    first = last_frame = None
    t0 = time.monotonic()
    pose_report = 0.
    timing = deque(maxlen=1024)
    floor_frames = 0
    last_label = None
    enc = None
    detector = PersonDetector(bool(floor_test))
    try:
        if preview_enabled:
            enc = PreviewEncoder(H264_COMMAND, sys.stderr, optional=continuous)
        while rclpy.ok() and (continuous or time.monotonic()-t0 < 125) and not (directory / 'stop').exists():
            rclpy.spin_once(n,timeout_sec=.01)
            if family:
                family.release_after_answer()
            if case_decision is not None:
                break
            if family and family.phase == 'idle' and notifier.questions:
                family.ask(notifier.questions.popleft())
            if enc and notifier and notifier.accepted.is_set() and enc.process:
                enc.close()
                display_heartbeat()
            if not view.stable(time.monotonic()):
                if time.monotonic() - pose_report > 3:
                    log('pose wait', view.action)
                    pose_report = time.monotonic()
                if not continuous and (time.monotonic() - (last_frame or t0) > (3 if last_frame else 14)):
                    break
                continue
            capstart = time.monotonic()
            attempts += 1
            im = capture_standing_frame(n, client, req, view, capstart)
            if im is None:
                continue
            tick = time.monotonic()
            detections, candidates = detector.infer(im)
            num = len(detections)
            conf = max((score for box, score in detections), default=0.0)
            fallen = floor_test.update(capstart, candidates, [box for box, _ in detections
                                       if box not in candidates]) if floor_test else []
            if floor_test:
                label = 2 if fallen else int(bool(detections))
                new_fall = not case_started and announcement.update(
                    capstart, fallen, candidates, [box for box, _ in detections])
                if case_once and new_fall:
                    case_started = True
                if new_fall and not family:
                    announce_floor(frames+1)
                if label != last_label or frames % 24 == 0:
                    log('FLOOR', frames+1, round(capstart-t0, 2), ('EMPTY','PERSON','ON_FLOOR')[label],
                        'people', num, 'candidates', len(candidates), 'boxes', [box for box, _ in detections])
                last_label = label
                floor_frames += bool(fallen)
            if enc and (not notifier or not notifier.accepted.is_set()) and enc.ready():
                rate = frames / max(.001, time.monotonic() - first) if first else 0.
                canvas = screen_frame(im, detections, fallen, view.target, badges, last_label, frames + 1, rate)
                enc.write(canvas.tobytes())
            frames += 1
            boxed += int(num > 0)
            highest = max(highest, conf)
            last_frame = time.monotonic()
            first = first or capstart
            timing.append(last_frame - tick)
            if frames < 4 or frames % 20 == 0:
                log('LIVE', frames, 'with_person', boxed, 'now', num, 'max_conf', round(highest, 3),
                    'fps', round(frames / (last_frame - first), 2), 'pose', view.action)


        if not continuous and not frames:
            raise RuntimeError('No annotated frames sent')
    finally:
        if family and 'T1_FALL_SESSION' not in os.environ:
            # The packaged CLI restores independently in systemd ExecStopPost.
            import subprocess
            subprocess.run([sys.executable, str(directory / 'graphical_voice.py'), family.voice.tag], check=True)
        if enc is not None:
            enc.close()
        if notifier:
            notifier.close()
        if floor_test:
            log('FLOOR_FRAMES', floor_frames)
        log('SUMMARY', json.dumps(dict(
            frames=frames, with_person=boxed, peak_conf=highest, attempts=attempts,
            span=round(last_frame - first, 2) if first else 0,
            median_infer_render_ms=round(1000 * np.median(timing), 2) if timing else None)))
        n.destroy_node()
        rclpy.try_shutdown()
        detector.close()

    if case_once:
        if case_decision in ('yes', 'no_reply') and not notifier.accepted.is_set():
            raise RuntimeError('SMS submission unconfirmed; inspect the case ledger before any retry')
        if case_decision not in ('yes', 'no_reply', 'no', 'unclear'):
            raise RuntimeError(f'Fall case ended without a completed answer window: {case_decision}')


if __name__ == '__main__':
    main()
