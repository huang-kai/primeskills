"""点歌台联动：保持站立形态，歌曲结束时恢复原站立状态和灯光。"""
import json
import logging
import random
import time

from ..support import AudioError

logger = logging.getLogger('primebuddy.skills.diangetai.music_effects')

# 来自当前 T1 部署接口的枚举；动作、运行状态和灯光模式不是同一套编号。
QUAD_STAND_ACTION = 101
QUAD_REST_ACTIONS = {1, 110}  # 被动模式（还须确认四足形态）、四足趴下。
QUAD_LOCOMOTION_ACTION = 102
QUAD_MIMIC_ACTION = 119
BIPED_STAND_ACTION = 201
BIPED_WBC_ACTION = 300
BIPED_MIMIC_ACTION = 208
BIPED_STANDING_ACTIONS = {BIPED_STAND_ACTION, BIPED_WBC_ACTION}
ACTION_RUNNING = 100
BLINK_LIGHT_STATES = ((255, 0, 0, 60, 500), (255, 45, 0, 45, 500), (255, 0, 60, 0, 500))
HAPPY_EMOTION_IDS = (90, 16, 17, 102, 18, 19)
EMPTY_EMOTION_ID = 13


class DanceController:
    # 四足趴卧可准备起身；已站立的双足/四足保留原模式，不互相变形。
    stand_action = QUAD_STAND_ACTION
    dance_action = QUAD_MIMIC_ACTION
    motion_name = 'QUAD_MIMIC'
    standing_actions = {QUAD_STAND_ACTION, QUAD_LOCOMOTION_ACTION}
    posture_name = '四足'

    def select_posture(self, action):
        if action not in BIPED_STANDING_ACTIONS | {QUAD_STAND_ACTION, QUAD_LOCOMOTION_ACTION} | QUAD_REST_ACTIONS:
            raise AudioError('当前模式不支持点歌起身或舞蹈；仅接受四足趴卧或稳定站立。')
        biped = action in BIPED_STANDING_ACTIONS
        self.posture_name = '双足' if biped else '四足'
        self.standing_actions = BIPED_STANDING_ACTIONS if biped else {QUAD_STAND_ACTION, QUAD_LOCOMOTION_ACTION}
        self.stand_action = QUAD_STAND_ACTION if action in QUAD_REST_ACTIONS else action
        self.dance_action = BIPED_MIMIC_ACTION if biped else QUAD_MIMIC_ACTION
        self.motion_name = 'BIPED_MIMIC_POPPING' if biped else 'QUAD_MIMIC'

    def require_resource(self):
        motions = self.rpc('GetMcMotions', self.request('GetMcMotions')).motion
        candidate = next((m for m in motions if m.tag == self.motion_name), None)
        if candidate is None or candidate.intr_info.intr_type.value != 1:
            raise AudioError('未取得可自由打断的 ' + self.motion_name + ' 资源。')

    action_descriptions = {101: 'QUADRUPED_STAND_DEFAULT', 102: 'QUADRUPED_LOCOMOTION_DEFAULT',
                           201: 'BIPED_STAND_DEFAULT', 300: 'BIPED_LOCOMOTION_WBC'}

    def __init__(self, robot):
        try:
            from aimdk_msgs.msg import McCommonState, RGBState
            from aimdk_msgs.srv import GetMcAction, GetMcMotions, SetMcAction, SetMcMotion, LedStripCommand
        except ImportError:
            raise AudioError('目标原厂消息包缺少舞蹈或灯光接口，联动不可用。') from None
        self.robot = robot
        self.types = {t.__name__: t for t in (GetMcAction, GetMcMotions, SetMcAction, SetMcMotion, LedStripCommand)}
        self.clients = {name: robot.node.create_client(t, '/aimdk_5Fmsgs/srv/' + name)
                        for name, t in self.types.items()}
        self.motion_state = self.light_state = None
        self.motion_received_at = self.light_received_at = 0
        self.subscriptions = [robot.node.create_subscription(McCommonState, '/aima/mc/common/state', self.on_mc, robot.qos),
                              robot.node.create_subscription(RGBState, '/aima/hal/rgb/state', self.on_rgb, robot.qos)]

    def on_mc(self, msg):
        self.motion_state, self.motion_received_at = msg, time.monotonic()

    def on_rgb(self, msg):
        # 保存完整灯光状态：模式、RGB、周期；恢复时不能只还原颜色。
        self.light_state = tuple(getattr(msg, k) for k in ('display_mode', 'r', 'g', 'b', 'period'))
        self.light_received_at = time.monotonic()

    def rpc(self, name, request, cleanup=False):
        client = self.clients[name]
        deadline = time.monotonic() + 8
        while not client.service_is_ready():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError(name + ' 服务未就绪。')
        future = client.call_async(request)
        while not future.done():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError(name + ' 请求超时，执行结果未知；未重发。')
        res = future.result()
        # 当前部署的三个响应头层级不同，不能统一按一个字段解析。
        if name == 'LedStripCommand':
            header = res.header.header
        elif name == 'GetMcAction':
            header = res.header
        else:
            header = res.response.header
        if header.code != 0:
            raise AudioError(name + ' 拒绝请求，code=' + str(header.code))
        return res

    def request(self, name):
        req = self.types[name].Request()
        header = req.request.header if name in {'GetMcAction', 'LedStripCommand'} else req.header
        header.stamp = self.robot.node.get_clock().now().to_msg()
        return req

    def action(self, cleanup=False):
        return self.rpc('GetMcAction', self.request('GetMcAction'), cleanup).info

    def inspect(self):
        deadline = time.monotonic() + 6
        while self.motion_state is None or self.light_state is None:
            self.robot.spin()
            if time.monotonic() >= deadline:
                raise AudioError('未取得运控与灯条状态。')
        info = self.action()
        self.select_posture(info.current_action.value)
        self.require_resource()
        result = {'action': info.current_action.value, 'action_desc': info.action_desc,
                  'status': info.status.value, 'rgb': self.light_state,
                  'posture': self.posture_name, 'motion': self.motion_name}
        logger.info('EFFECT_PREFLIGHT %s', json.dumps(result, ensure_ascii=False))
        return result

    def require_stand(self):
        info = self.action()
        now = time.monotonic()
        if (info.current_action.value not in self.standing_actions or info.status.value != ACTION_RUNNING
                or self.motion_state is None or now - self.motion_received_at > 2
                or self.motion_state.is_moving or self.motion_state.is_fallen
                or now - self.light_received_at > 2):
            raise AudioError('须处于本轮选择的稳定' + self.posture_name + '站立且反馈新鲜；未自动变形。')
        return self.light_state

    def require_ready(self):
        """只读准备检查；被动模式不等于趴卧，还须核对形态和当前反馈。"""
        info = self.action()
        self.select_posture(info.current_action.value)
        state = self.motion_state
        now = time.monotonic()
        expected_form = 2 if self.posture_name == '双足' else 1
        if (info.status.value != ACTION_RUNNING or state is None
                or now - self.motion_received_at > 2 or now - self.light_received_at > 2
                or state.is_moving or state.is_fallen
                or state.form_state.current_form != expected_form
                or state.lift_state.is_lifted or state.held_state.is_held
                or state.action_info.current_action.value != info.current_action.value):
            raise AudioError('姿态未确认静止、反馈过期、被提起或形态不匹配；未发送起身指令。')
        return info.current_action.value

    def set_stand(self, cleanup=False):
        req = self.request('SetMcAction')
        req.source = 'sdk_rpc'
        req.command.action.value = self.stand_action
        req.command.action_desc = self.action_descriptions[self.stand_action]
        self.rpc('SetMcAction', req, cleanup)

    def start(self):
        req = self.request('SetMcMotion')
        req.source, req.motion = 'sdk_rpc', self.motion_name
        req.type = req.MIMIC_QY
        # 兼容已核对的两种消息结构；有operation字段时明确要求执行。
        if hasattr(req, 'operation'):
            req.operation = req.EXECUTION
        req.interrupt = False
        res = self.rpc('SetMcMotion', req)
        logger.info('MOTION_SUBMITTED %s', res.response.task_id)
        deadline = time.monotonic() + 8
        while True:
            info = self.action()
            if info.current_action.value == self.dance_action and info.status.value == ACTION_RUNNING:
                logger.info('DANCE_MOTION_MODE_CONFIRMED %s', self.motion_name)
                return
            if time.monotonic() >= deadline:
                raise AudioError('未确认' + self.motion_name + '进入运行，尝试恢复。')
            self.robot.spin()

    def light(self, state, cleanup=False):
        req = self.request('LedStripCommand')
        req.led_strip_mode, req.r, req.g, req.b, req.period = state
        res = self.rpc('LedStripCommand', req, cleanup)
        if res.header.status.value != 1:
            raise AudioError('灯条未确认请求成功。')

    def restore_stand(self):
        # 只从本轮形态的站立或舞蹈状态恢复，防止覆盖外部变形或其他动作。
        info = self.action(cleanup=True)
        if (self.motion_state is None or time.monotonic() - self.motion_received_at > 2
                or self.motion_state.is_fallen):
            raise AudioError('运控反馈过期或已跌倒，未强制恢复站立。')
        if info.current_action.value in self.standing_actions:
            if info.status.value != ACTION_RUNNING or self.motion_state.is_moving:
                raise AudioError('当前站立形态未确认静止，未强制恢复。')
        elif info.current_action.value != self.dance_action or info.status.value != ACTION_RUNNING:
            raise AudioError('运控已转入其他模式；未强制覆盖，需现场确认姿态。')
        if info.current_action.value != self.stand_action:
            self.set_stand(cleanup=True)
        self.wait_stand()

    def wait_stand(self, after=0):
        deadline = time.monotonic() + 15
        while True:
            info = self.action(cleanup=True)
            if (info.current_action.value == self.stand_action and info.status.value == ACTION_RUNNING
                    and self.motion_state is not None and time.monotonic() - self.motion_received_at < 2
                    and self.motion_received_at >= after
                    and self.motion_state.action_info.current_action.value == self.stand_action
                    and not self.motion_state.is_moving and not self.motion_state.is_fallen):
                logger.info('DANCE_STAND_RESTORED %s %s', self.posture_name, self.stand_action)
                return
            if time.monotonic() >= deadline:
                raise AudioError('未确认' + self.posture_name + '站立恢复。')
            self.robot.spin(cancellable=False)

    def restore_light(self, original, owned):
        if owned is None:
            return
        deadline = time.monotonic() + 3
        # 服务响应可能早于灯光话题回调；有界等待匹配，避免误判或覆盖系统灯光。
        while time.monotonic() - self.light_received_at > 2 or self.light_state != owned:
            self.robot.spin(cancellable=False)
            if time.monotonic() >= deadline:
                raise AudioError('灯光反馈未匹配本任务，未覆盖：当前=' + str(self.light_state)
                                 + '，预期=' + str(owned))
        self.light(original, cleanup=True)
        after = time.monotonic()
        deadline = after + 5
        while self.light_state != original or self.light_received_at < after:
            self.robot.spin(cancellable=False)
            if time.monotonic() >= deadline:
                raise AudioError('未确认灯光恢复。')
        logger.info('RGB_RESTORED %s', original)


class MusicEffects(DanceController):
    """动作、灯光和喜悦表情跟随歌曲生命周期；时限统一由音乐会话控制。"""
    happy_ids = HAPPY_EMOTION_IDS
    reset_emotion_id = EMPTY_EMOTION_ID

    def __init__(self, robot, record):
        try:
            from aimdk_msgs.srv import PlayEmotion
        except ImportError:
            raise AudioError('目标原厂消息包缺少表情接口，联动不可用。') from None
        super().__init__(robot)
        self.emotion_client = robot.node.create_client(PlayEmotion, '/aimdk_5Fmsgs/srv/PlayEmotion')
        self.emotion_submitted = False
        self.record = record
        self.original_light_state = self.last_owned_light_state = None
        self.next_color_at = self.color_index = 0
        self.restore_failed = False
        self.restore_error = ''
        self.stand_pending = False
        if record.exists():
            raise AudioError('存在音乐联动恢复记录；未再次接管。')

    def inspect(self):
        result = super().inspect()
        if not self.emotion_client.wait_for_service(timeout_sec=5):
            raise AudioError('SDK 表情播放服务不可用。')
        return result

    def prepare(self):
        self.robot.spin(0)
        action = self.require_ready()
        self.require_resource()
        if self.require_ready() != action:
            raise AudioError('准备期间姿态已变化，未发送起身指令。')
        if action in self.standing_actions:
            logger.info('DANCE_POSTURE_PRESERVED %s %s', self.posture_name, self.stand_action)
            return
        # 记录先于写入请求；超时或中断可能已生效，保留记录且绝不重发起身。
        with self.record.open('x') as output:
            self.record.chmod(0o600)
            json.dump({'phase': 'preparing_stand', 'initial_action': action,
                       'stand_action': self.stand_action, 'motion': self.motion_name}, output)
        self.stand_pending = True
        logger.info('QUAD_STAND_REQUEST')
        requested_at = time.monotonic()
        self.set_stand()
        self.wait_stand(after=requested_at)
        self.record.unlink()
        self.stand_pending = False

    def ready(self):
        return self.require_stand()

    def dance(self):
        self.require_stand()
        self.start()

    def happy(self):
        emotion_id = random.choice(self.happy_ids)
        self.emotion_submitted = True
        self.play_emotion(emotion_id)
        logger.info('HAPPY_EMOTION_REQUEST_ACCEPTED %s', emotion_id)

    def play_emotion(self, emotion_id, cleanup=False):
        from aimdk_msgs.srv import PlayEmotion
        request = PlayEmotion.Request()
        request.header.header.stamp = self.robot.node.get_clock().now().to_msg()
        request.type = 'emotion'
        request.emotion_ids = [emotion_id]
        request.priority = 0
        request.loop_count = 0
        self.robot.call(self.emotion_client, request, cancellable=not cleanup)

    def begin(self):
        original = self.require_stand()
        # 记录写入成功后才能启动动作；残留记录不能被新会话覆盖。
        with self.record.open('x') as output:
            self.record.chmod(0o600)
            json.dump({'motion': self.motion_name, 'stand_action': self.stand_action, 'original_rgb': original,
                       'reset_emotion_id': self.reset_emotion_id}, output)
        self.original_light_state = original
        self.dance()
        # 每首只随机一次；表情循环显示，动作重启和灯光换色不重新抽取。
        self.happy()  # 请求超时也可能生效，清理时仍须复位表情。
        self.next_color_at = self.color_index = 0
        self.next_motion_check_at = time.monotonic() + 1

    def tick(self):
        self.robot.spin(0)
        if self.original_light_state is None:
            return
        if time.monotonic() >= self.next_motion_check_at:
            info = self.action()
            if (self.motion_state is None or time.monotonic() - self.motion_received_at > 2
                    or self.motion_state.is_fallen):
                raise AudioError('舞蹈期间运控反馈过期或已跌倒，停止音乐并保留恢复检查。')
            if info.current_action.value in self.standing_actions and info.status.value == ACTION_RUNNING:
                # 原动作结束才重新执行，运行中的扭腰不叠加或强行打断。
                self.restore_stand()
                self.require_stand()
                self.start()
            elif info.current_action.value != self.dance_action or info.status.value != ACTION_RUNNING:
                raise AudioError('舞蹈期间运控进入其他模式；停止音乐并尝试恢复。')
            self.next_motion_check_at = time.monotonic() + 1
        if time.monotonic() < self.next_color_at:
            return
        self.last_owned_light_state = BLINK_LIGHT_STATES[self.color_index % len(BLINK_LIGHT_STATES)]
        self.light(self.last_owned_light_state)
        self.color_index += 1
        self.next_color_at = time.monotonic() + 1

    def stop_music(self):
        if self.stand_pending:
            raise AudioError('四足起身结果未确认；未播放歌曲，恢复记录保留，未重复发送起身指令。')
        if self.original_light_state is None:
            return
        if self.restore_failed:
            # 恢复结果不明时，后续 finally 只报告原始错误，不盲目重复控制。
            raise AudioError(self.restore_error + '；记录保留，未重复发送控制。')
        errors = []
        # 三项恢复独立尝试；灯光恢复失败也不能让喜悦表情一直循环。
        restorations = [self.restore_stand,
                        lambda: self.restore_light(self.original_light_state, self.last_owned_light_state)]
        if self.emotion_submitted:
            restorations.append(lambda: self.play_emotion(self.reset_emotion_id, cleanup=True))
        for restore in restorations:
            try:
                restore()
            except Exception as error:
                errors.append(str(error))
        if errors:
            self.restore_failed = True
            self.restore_error = '音乐联动恢复未完成：' + '；'.join(errors)
            raise AudioError(self.restore_error)
        self.record.unlink()
        self.emotion_submitted = False
        self.original_light_state = self.last_owned_light_state = None
        logger.info('MUSIC_EFFECTS_RESTORED')

    def close(self):
        for subscription in self.subscriptions:
            self.robot.node.destroy_subscription(subscription)
        for client in [*self.clients.values(), self.emotion_client]:
            self.robot.node.destroy_client(client)


class Q1MusicEffects(MusicEffects):
    """Q1 使用原厂预设上肢舞蹈；每段完成后再选下一段。"""
    stand_action = 1002
    standing_actions = {1002}
    dance_action = 1005
    motion_name = 'Q_DANCE_A/B/C'
    dance_motions = (3046, 3047, 3048)
    posture_name = '人形'
    happy_ids = (3003,)
    reset_emotion_id = 3001
    action_descriptions = {1002: 'BIPED_WALK_RUN', 1005: 'BIPED_WHOLE_BODY_CTRL'}

    def __init__(self, robot, record, music_playing=lambda: False):
        try:
            from aimdk_msgs.srv import SetMcPresetMotion, GetMcPresetMotionState
        except ImportError:
            raise AudioError('Q1 缺少原厂预设动作或任务查询接口，联动不可用。') from None
        super().__init__(robot, record)
        self.music_playing = music_playing
        self.task_id = self.last_motion = None
        self.completed_wait_at = None
        self.last_task_diagnostic = None
        self.task_submitted_at = 0
        self.task_owner_seen = self.task_running_seen = False
        self.mode_task_id = None
        self.mode_previous_task_id = 0
        self.mode_submitted_at = 0
        self.last_control_at = -1
        self.mode_owned = self.command_unknown = self.control_lost = False
        for kind in (SetMcPresetMotion, GetMcPresetMotionState):
            self.types[kind.__name__] = kind
            self.clients[kind.__name__] = robot.node.create_client(kind, '/aimdk_5Fmsgs/srv/' + kind.__name__)

    def require_resource(self):
        for name in ('SetMcPresetMotion', 'GetMcPresetMotionState'):
            if not self.clients[name].wait_for_service(timeout_sec=5):
                raise AudioError('Q1 原厂 ' + name + ' 接口不可用。')
        resources = self.rpc('GetMcMotions', self.request('GetMcMotions')).motion
        for motion in (0, *self.dance_motions):
            registered = next((item for item in resources if item.tag == str(motion)), None)
            if (registered is None or registered.type.value != 1
                    or registered.intr_info.intr_type.value != 1):
                raise AudioError('Q1 原厂预设资源 ' + str(motion) + ' 缺失或不能自由打断。')

    def request(self, name):
        if name != 'GetMcPresetMotionState':
            return super().request(name)
        request = self.types[name].Request()
        request.request.header.stamp = self.robot.node.get_clock().now().to_msg()
        return request

    def rpc(self, name, request, cleanup=False):
        if name not in {'SetMcAction', 'SetMcPresetMotion', 'GetMcPresetMotionState'}:
            return super().rpc(name, request, cleanup)
        client = self.clients[name]
        writing = name != 'GetMcPresetMotionState'
        deadline = time.monotonic() + 8
        while not client.service_is_ready():
            if writing and not cleanup and not self.music_playing():
                return None
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError(name + ' 服务未就绪。')
        if writing:
            while time.monotonic() - self.last_control_at < .12:
                self.robot.spin(cancellable=not cleanup)
                if not cleanup and not self.music_playing():
                    return None
            # 检查协作取消并处理已到达的 END，再决定是否写入。
            self.robot.spin(0, cancellable=not cleanup)
        # 等待服务期间可能收到音乐 END；必须在实际写入前再次核对。
        if writing and not cleanup and not self.music_playing():
            return None
        if writing:
            if not self.write_owned(cleanup):
                return None
            if not cleanup and not self.music_playing():
                return None
            self.command_unknown = True
            self.last_control_at = time.monotonic()
        future = client.call_async(request)
        while not future.done():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError(name + ' 请求超时，执行结果未知；未重发。')
        response = future.result().response
        if writing:
            self.command_unknown = False
        if name == 'GetMcPresetMotionState':
            return response
        if response.header.code != 0 or response.state.reason != 0:
            raise AudioError(name + ' 拒绝请求。')
        if writing and response.state.value not in (1, 400):
            raise AudioError(name + ' 未接受请求，state=' + str(response.state.value))
        return response

    def write_owned(self, cleanup):
        if getattr(self, 'mode_task_id', None) is not None:
            while True:
                fresh = (self.motion_state is not None and time.monotonic() - self.motion_received_at <= 2
                         and self.motion_received_at >= self.mode_submitted_at)
                info = getattr(self.motion_state, 'task_info', None) if fresh else None
                task_id = getattr(info, 'task_id', None)
                if fresh and task_id == self.mode_task_id:
                    return True
                if fresh and task_id not in (self.mode_previous_task_id, self.mode_task_id):
                    self.control_lost = True
                    raise AudioError('Q1 全身控制模式已被其他任务接管；未提交预设动作。')
                if time.monotonic() - self.mode_submitted_at > 8:
                    raise AudioError('Q1 自有模式任务归属未确认，恢复记录保留。')
                if not cleanup and not self.music_playing():
                    return False
                self.robot.spin(cancellable=not cleanup)
        if getattr(self, 'task_id', None) is not None and not self.preset_owned():
            raise AudioError('Q1 当前预设任务归属未确认，恢复记录保留；未发送覆盖动作。')
        return True

    def safe_action(self, stationary=False):
        info = self.action(cleanup=True)
        state = self.motion_state
        if (state is None or time.monotonic() - self.motion_received_at > 2 or state.is_fallen
                or stationary and state.is_moving):
            raise AudioError('Q1 运控反馈过期、跌倒或未确认静止；未继续控制。')
        for name, field in (('lift_state', 'is_lifted'), ('held_state', 'is_held')):
            extra = getattr(state, name, None)
            if extra is not None and getattr(extra, field):
                raise AudioError('Q1 被提起或抱持；未继续控制。')
        if info.current_action.value not in (1002, 1005):
            self.control_lost = True
            raise AudioError('Q1 已进入其他动作；未覆盖外部控制。')
        return info

    def set_mode(self, action, cleanup=False):
        if cleanup and action == 1002:
            self.robot.spin(0, cancellable=False)
            if self.safe_action(stationary=True).current_action.value == 1002:
                return True
        request = self.request('SetMcAction')
        request.source = 'sdk_rpc'
        request.command.action.value = action
        request.command.action_desc = self.action_descriptions[action]
        response = self.rpc('SetMcAction', request, cleanup)
        if response is not None and action == 1005:
            previous = getattr(self, 'task_id', None)
            self.mode_task_id = response.task_id or previous
            self.mode_previous_task_id = previous or 0
            self.mode_submitted_at = self.last_control_at
            self.task_id = None
            self.task_owner_seen = self.task_running_seen = False
        return response is not None

    def wait_mode(self, action, cleanup=False, after=0):
        targets = action if isinstance(action, tuple) else (action,)
        deadline = time.monotonic() + 15
        while True:
            self.robot.spin(cancellable=not cleanup)
            info = self.safe_action()
            state_action = getattr(self.motion_state, 'action_info', None)
            if (info.current_action.value in targets and info.status.value == ACTION_RUNNING
                    and self.motion_received_at >= after
                    and not self.motion_state.is_moving
                    and (state_action is None or state_action.current_action.value == info.current_action.value)):
                return info.current_action.value
            if not cleanup and not self.music_playing():
                return False
            if time.monotonic() >= deadline:
                raise AudioError('Q1 未确认模式切换完成；恢复记录保留。')

    def submit_preset(self, motion, cleanup=False):
        request = self.request('SetMcPresetMotion')
        request.source, request.motion.value = 'sdk_rpc', motion
        request.area.value, request.interrupt, request.ani_path = 0, cleanup, ''
        response = self.rpc('SetMcPresetMotion', request, cleanup)
        if response is None:
            return False
        if not response.task_id:
            self.command_unknown = True
            raise AudioError('Q1 预设动作没有返回有效任务 ID，恢复记录保留。')
        self.task_id = response.task_id
        self.mode_task_id = None
        self.last_motion = motion
        self.task_submitted_at = self.last_control_at
        self.task_owner_seen = self.task_running_seen = False
        with self.record.open() as source:
            saved = json.load(source)
        saved.update(task_id=self.task_id, preset_motion=motion)
        from ..support import write_json
        write_json(self.record, saved)
        logger.info('Q1_PRESET_SUBMITTED %s %s', motion, self.task_id)
        self.task_state(cleanup=cleanup)
        return True

    def preset_owned(self):
        fresh = (self.motion_state is not None and time.monotonic() - self.motion_received_at <= 2
                 and self.motion_received_at >= self.task_submitted_at)
        task_info = getattr(self.motion_state, 'task_info', None) if fresh else None
        task_id = getattr(task_info, 'task_id', None)
        if fresh and self.task_owner_seen and task_id != self.task_id:
            self.control_lost = True
            raise AudioError('Q1 预设动作已被其他任务接管；未覆盖外部控制。')
        if fresh and task_id == self.task_id:
            self.task_owner_seen = True
            return True
        return False

    def task_state(self, cleanup=False):
        self.robot.spin(0, cancellable=not cleanup)
        request = self.request('GetMcPresetMotionState')
        request.request.task_id = self.task_id
        response = self.rpc('GetMcPresetMotionState', request, cleanup)
        fresh = (self.motion_state is not None and time.monotonic() - self.motion_received_at <= 2
                 and self.motion_received_at >= self.task_submitted_at)
        task_info = getattr(self.motion_state, 'task_info', None) if fresh else None
        diagnostic = {'request_task_id': self.task_id, 'response_task_id': response.task_id,
                      'code': response.header.code, 'state': response.state.value,
                      'reason': response.state.reason, 'mc_task_id': getattr(task_info, 'task_id', None),
                      'mc_fresh': fresh}
        if diagnostic != self.last_task_diagnostic:
            logger.info('Q1_PRESET_STATUS %s', json.dumps(diagnostic))
            self.last_task_diagnostic = diagnostic
        if response.header.code != 0 or response.state.reason != 0:
            raise AudioError('GetMcPresetMotionState 拒绝请求。')
        if response.task_id not in (0, self.task_id):
            self.control_lost = True
            raise AudioError('Q1 预设动作任务未确认正常完成；未覆盖外部控制。')
        if self.preset_owned():
            if response.state.value == 400:
                self.task_running_seen = True
                return 400
            if response.state.value != 1:
                self.control_lost = True
                raise AudioError('Q1 预设动作任务未确认正常完成；未覆盖外部控制。')
            if self.task_running_seen:
                return 1
            if self.last_motion == 0:
                info = self.safe_action()
                state_action = getattr(self.motion_state, 'action_info', None)
                if (self.preset_owned() and info.current_action.value == 1002
                        and info.status.value == ACTION_RUNNING and not self.motion_state.is_moving
                        and state_action is not None and state_action.current_action.value == 1002):
                    return 1
        if time.monotonic() - self.task_submitted_at > 8:
            raise AudioError('Q1 原厂预设状态未证实本任务生命周期，恢复记录保留。')
        return None

    def start(self):
        if not self.music_playing():
            return False
        info = self.safe_action()
        if info.status.value != ACTION_RUNNING or self.motion_state.is_moving:
            if self.task_id is None:
                raise AudioError('Q1 模式未稳定，未提交预设舞蹈。')
            return False
        if info.current_action.value == 1002:
            requested_at = time.monotonic()
            if not self.set_mode(1005):
                return False
            self.mode_owned = True
            if not self.wait_mode(1005, after=requested_at):
                return False
        motion = random.choice(tuple(m for m in self.dance_motions if m != self.last_motion))
        return self.submit_preset(motion)

    def happy(self):
        while self.music_playing() and not self.task_running_seen:
            self.task_state()
            self.robot.spin()
        if self.music_playing():
            super().happy()

    def tick(self):
        self.robot.spin(0)
        if self.original_light_state is None or not self.music_playing():
            return
        self.safe_action()
        if time.monotonic() >= self.next_motion_check_at:
            if self.task_id is not None and self.task_state() == 1:
                if self.completed_wait_at is None:
                    self.completed_wait_at = time.monotonic()
                if self.start():
                    self.completed_wait_at = None
                elif self.music_playing() and time.monotonic() - self.completed_wait_at > 3:
                    raise AudioError('Q1 动作结束后未确认静止，停止续发舞蹈。')
            self.next_motion_check_at = time.monotonic() + .1
        if time.monotonic() >= self.next_color_at and self.music_playing():
            self.last_owned_light_state = BLINK_LIGHT_STATES[self.color_index % len(BLINK_LIGHT_STATES)]
            self.light(self.last_owned_light_state)
            self.color_index += 1
            self.next_color_at = time.monotonic() + 1

    def restore_stand(self):
        if self.command_unknown or self.control_lost:
            raise AudioError('Q1 动作结果未知或控制权已变化，恢复记录保留；未重复强制控制。')
        info = self.safe_action()
        if info.current_action.value == 1002:
            if self.task_id is not None and self.task_state(cleanup=True) != 1:
                raise AudioError('Q1 模式与预设任务状态不一致，恢复记录保留。')
            self.wait_mode(1002, cleanup=True, after=time.monotonic())
            return
        if not self.mode_owned:
            raise AudioError('Q1 当前全身控制模式不属于本任务，未强制恢复。')
        if self.task_id is not None:
            state = self.task_state(cleanup=True)
            if state is None and not self.preset_owned():
                raise AudioError('Q1 当前预设任务归属尚未确认，恢复记录保留；未发送覆盖动作。')
            # 默认姿态是原厂预设，不把取消 RPC 的接受响应当作归位完成。
            self.submit_preset(0, cleanup=True)
            deadline = time.monotonic() + 15
            while self.task_state(cleanup=True) != 1:
                self.robot.spin(cancellable=False)
                self.safe_action()
                if time.monotonic() >= deadline:
                    raise AudioError('Q1 默认姿态任务结束未确认，恢复记录保留。')
        requested_at = time.monotonic()
        if self.wait_mode((1002, 1005), cleanup=True, after=requested_at) == 1005:
            requested_at = time.monotonic()
            self.set_mode(1002, cleanup=True)
        self.wait_mode(1002, cleanup=True, after=requested_at)
        logger.info('DANCE_STAND_RESTORED Q1 1002')

    def select_posture(self, action):
        if action != self.stand_action:
            raise AudioError('Q1 舞蹈要求已处于稳定的 BIPED_WALK_RUN(1002)；不自动起身或切换模式。')

    def require_stand(self):
        self.require_ready()
        return self.light_state

    def require_ready(self):
        info = self.action()
        self.select_posture(info.current_action.value)
        state = self.motion_state
        now = time.monotonic()
        if (info.status.value != ACTION_RUNNING or state is None
                or now - self.motion_received_at > 2 or now - self.light_received_at > 2
                or state.is_moving or state.is_fallen):
            raise AudioError('Q1 姿态未确认稳定或反馈过期，未提交舞蹈。')
        # Q1 不要求 T1 的 form_state；部署版本提供的额外保护状态仍须核对。
        for name, field in (('lift_state', 'is_lifted'), ('held_state', 'is_held')):
            extra = getattr(state, name, None)
            if extra is not None and getattr(extra, field):
                raise AudioError('Q1 被提起或抱持，未提交舞蹈。')
        action = getattr(state, 'action_info', None)
        if action is not None and action.current_action.value != info.current_action.value:
            raise AudioError('Q1 动作反馈不一致，未提交舞蹈。')
        return info.current_action.value
