"""点歌台联动：保持站立形态，歌曲结束时恢复原站立状态和灯光。"""
import json
import random
import time

from primebuddy.voice_support.robot_audio import AudioError

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

    def __init__(self, robot):
        from aimdk_msgs.msg import McCommonState, RGBState
        from aimdk_msgs.srv import GetMcAction, GetMcMotions, SetMcAction, SetMcMotion, LedStripCommand
        self.robot = robot
        self.types = {t.__name__: t for t in (GetMcAction, GetMcMotions, SetMcAction, SetMcMotion, LedStripCommand)}
        self.clients = {name: robot.node.create_client(t, '/aimdk_5Fmsgs/srv/' + name)
                        for name, t in self.types.items()}
        self.motion_state = self.light_state = None
        self.motion_received_at = self.light_received_at = 0
        robot.node.create_subscription(McCommonState, '/aima/mc/common/state', self.on_mc, robot.qos)
        robot.node.create_subscription(RGBState, '/aima/hal/rgb/state', self.on_rgb, robot.qos)

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
        print('EFFECT_PREFLIGHT', json.dumps(result, ensure_ascii=False), flush=True)
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
        req.command.action_desc = {101: 'QUADRUPED_STAND_DEFAULT', 102: 'QUADRUPED_LOCOMOTION_DEFAULT',
                                   201: 'BIPED_STAND_DEFAULT', 300: 'BIPED_LOCOMOTION_WBC'}[self.stand_action]
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
        print('MOTION_SUBMITTED', res.response.task_id, flush=True)
        deadline = time.monotonic() + 8
        while True:
            info = self.action()
            if info.current_action.value == self.dance_action and info.status.value == ACTION_RUNNING:
                print('DANCE_MOTION_MODE_CONFIRMED', self.motion_name, flush=True)
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
                print('DANCE_STAND_RESTORED', self.posture_name, self.stand_action, flush=True)
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
        print('RGB_RESTORED', original, flush=True)


class MusicEffects(DanceController):
    """动作、灯光和喜悦表情跟随歌曲生命周期；时限统一由音乐会话控制。"""
    def __init__(self, robot, record):
        super().__init__(robot)
        from aimdk_msgs.srv import PlayEmotion
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

    def prepare_stand(self):
        action = self.require_ready()
        self.require_resource()
        if self.require_ready() != action:
            raise AudioError('准备期间姿态已变化，未发送起身指令。')
        if action in self.standing_actions:
            print('DANCE_POSTURE_PRESERVED', self.posture_name, self.stand_action, flush=True)
            return
        # 记录先于写入请求；超时或中断可能已生效，保留记录且绝不重发起身。
        with self.record.open('x') as output:
            self.record.chmod(0o600)
            json.dump({'phase': 'preparing_stand', 'initial_action': action,
                       'stand_action': self.stand_action, 'motion': self.motion_name}, output)
        self.stand_pending = True
        print('QUAD_STAND_REQUEST', flush=True)
        requested_at = time.monotonic()
        self.set_stand()
        self.wait_stand(after=requested_at)
        self.record.unlink()
        self.stand_pending = False

    def play_emotion(self, emotion_id, cleanup=False):
        from aimdk_msgs.srv import PlayEmotion
        request = PlayEmotion.Request()
        request.header.header.stamp = self.robot.node.get_clock().now().to_msg()
        request.type = 'emotion'
        request.emotion_ids = [emotion_id]
        request.priority = 0
        request.loop_count = 0
        self.robot.call(self.emotion_client, request, cancellable=not cleanup)

    def begin_music(self):
        original = self.require_stand()
        # 记录写入成功后才能启动动作；残留记录不能被新会话覆盖。
        with self.record.open('x') as output:
            self.record.chmod(0o600)
            json.dump({'motion': self.motion_name, 'stand_action': self.stand_action, 'original_rgb': original,
                       'reset_emotion_id': EMPTY_EMOTION_ID}, output)
        self.original_light_state = original
        self.start()
        # 每首只随机一次；表情循环显示，动作重启和灯光换色不重新抽取。
        emotion_id = random.choice(HAPPY_EMOTION_IDS)
        self.emotion_submitted = True  # 请求超时也可能生效，清理时仍需切回空表情。
        self.play_emotion(emotion_id)
        print('HAPPY_EMOTION_REQUEST_ACCEPTED', emotion_id, flush=True)
        self.next_color_at = self.color_index = 0
        self.next_motion_check_at = time.monotonic() + 1

    def tick(self):
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
            restorations.append(lambda: self.play_emotion(EMPTY_EMOTION_ID, cleanup=True))
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
        print('MUSIC_EFFECTS_RESTORED', flush=True)
