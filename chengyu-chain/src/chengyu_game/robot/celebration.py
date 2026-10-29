"""单次游戏反馈；实体动作须按型号现场验收，所有写入保留恢复记录。"""
from pathlib import Path
import logging
import math
import time

from ..support import AudioError, Cancelled, SetupError, read_json, write_json
from .device import detect_device
from .expression import play_expression
from .motion_rules import load_quad_route, valid_quad_route
from .transport import RequestRejected


# 表情编号、运控模式和预设动作使用不同接口，不能互换。
FACES = {'T1': {'happy': 90, 'sad': 21, 'reset': 13},
         'Q1': {'happy': 3003, 'sad': 3018, 'reset': 3001}}
# Q1 难过 3018 已在当前固件 emotions.yaml 核实，旧 SDK 的表情表不完整。
OUTCOMES = {'success': 'happy', 'win': 'happy', 'failure': 'sad', 'lose': 'sad'}
CHARGING_MASK = 1 | (1 << 25) | (1 << 26)
PROFILES = {
    'T1_biped': {'initial': {201, 300}, 'mode': 300, 'preset': 2002, 'form': 2,
                 'enum': 'BOTH_HANDS_ACTION_HAPPY'},
    'T1_quad': {'initial': {101, 102}, 'mode': 114, 'form': 1,
                'enum': 'QUADRUPED_LOCOMOTION_PLEASURE'},
    'Q1': {'initial': {1002}, 'mode': 1005, 'preset': 3005,
           'enum': 'Q_SHOW_HAPPINESS'},
}
SAD_PROFILES = {
    'T1_biped': {'initial': {201, 300}, 'mode': 300, 'preset': 2033, 'form': 2,
                 'enum': 'HUMANOID_SAD'},
    'T1_quad': {'initial': {101, 102}, 'mode': 119, 'form': 1,
                'enum': 'QUADRUPED_MIMIC', 'motion': 'QUAD_MIMIC_SAD'},
}
ACTION_NAMES = {101: 'QUADRUPED_STAND_DEFAULT', 102: 'QUADRUPED_LOCOMOTION_DEFAULT',
                121: 'QUADRUPED_LOCOMOTION_STEP',
                114: 'QUADRUPED_LOCOMOTION_PLEASURE', 201: 'BIPED_STAND_DEFAULT',
                300: 'BIPED_LOCOMOTION_WBC', 1002: 'BIPED_WALK_RUN',
                1005: 'BIPED_WHOLE_BODY_CTRL'}


class Celebration:
    def __init__(self, robot, runtime, config):
        self.robot, self.path = robot, Path(runtime) / 'effects.json'
        self.config = config.get('celebration', {})
        if not isinstance(self.config, dict):
            raise AudioError('celebration 配置须为对象。')
        self.config = self.config.copy()
        self.record = {}
        self.clients, self.types, self.subscriptions = {}, {}, []
        self.state, self.received_at = None, 0
        self.state_sequence = 0
        self.battery_status, self.battery_received_at = None, 0
        self.power_checked_at = float('-inf')
        self.last_control_at = -1
        self.device = None
        self.record_valid = True
        self.failure_reason = ''
        self.face_reset_rejected = False
        self.quad_routes = {}
        self.motion_cleanup_error = ''
        self.motion_status = {'status': 'disabled', 'reason': '身体动作未启用。'}

    @staticmethod
    def _profile(motion):
        profiles = SAD_PROFILES if motion.get('mood') == 'sad' else PROFILES
        return profiles[motion['profile']]

    def _initialize(self, motion=False):
        if self.device is None:
            self.device = detect_device()
        try:
            from aimdk_msgs.srv import PlayEmotion
            kinds = [PlayEmotion]
            if motion:
                from aimdk_msgs.msg import McCommonState, Bms
                from aimdk_msgs.srv import (GetMcAction, GetMcMotions, SetMcAction,
                                            SetMcPresetMotion, GetMcPresetMotionState, GetJointPowerState)
                kinds += [GetMcAction, GetMcMotions, SetMcAction,
                          SetMcPresetMotion, GetMcPresetMotionState, GetJointPowerState]
                if not self.subscriptions:
                    self.subscriptions.append(self.robot.node.create_subscription(
                        McCommonState, '/aima/mc/common/state', self._on_mc, self.robot.qos))
                    self.subscriptions.append(self.robot.node.create_subscription(
                        Bms, '/aima/hal/bms/state', self._on_bms, self.robot.qos))
            for kind in kinds:
                name = kind.__name__
                if name not in self.clients:
                    self.types[name] = kind
                    self.clients[name] = self.robot.node.create_client(kind, '/aimdk_5Fmsgs/srv/' + name)
        except ImportError:
            raise AudioError('原厂消息包缺少庆祝所需接口。') from None

    def _initialize_mimic(self):
        try:
            from aimdk_msgs.srv import SetMcMotion
        except ImportError:
            raise AudioError('原厂消息包没有 SetMcMotion，四足难过动作不可用。') from None
        if 'SetMcMotion' not in self.clients:
            self.types['SetMcMotion'] = SetMcMotion
            self.clients['SetMcMotion'] = self.robot.node.create_client(
                SetMcMotion, '/aimdk_5Fmsgs/srv/SetMcMotion')

    def start(self):
        if self.path.exists():
            raise AudioError('上次庆祝尚未确认清理，请先恢复；未覆盖记录。')
        if self.config.get('enabled', True):
            self._initialize()

    def _save(self):
        if self.record.get('expression') or self.record.get('motion'):
            write_json(self.path, self.record)
        else:
            self.path.unlink(missing_ok=True)
            self.record = {}

    def _on_mc(self, message):
        self.state, self.received_at = message, time.monotonic()
        self.state_sequence += 1
        motion = self.record.get('motion', {})
        if (motion.get('profile') == 'T1_quad' and motion.get('action') == 114
                and not motion.get('running_seen') and message.task_info.task_id == motion.get('task_id')
                and message.action_info.current_action.value == 114 and message.action_info.status.value == 100):
            motion['running_seen'] = True
            self._save()

    def _on_bms(self, message):
        # 不保留整帧：BMS 同时包含序列号等与动作保护无关的信息。
        self.battery_status, self.battery_received_at = getattr(message, 'status', None), time.monotonic()

    def _request(self, name):
        request = self.types[name].Request()
        if name == 'GetMcMotions' and self.device.series == 'Q1':
            if not hasattr(request, 'form_state') or getattr(request.form_state, 'UNKNOWN', None) != 0:
                raise AudioError('Q1 GetMcMotions 接口不匹配：缺少 form_state；请成套加载当前原厂接口库。')
            # UNKNOWN=0 查询全部形态；包括默认复位及当前机型的预设资源。
            request.form_state.current_form = request.form_state.UNKNOWN
        header = request.request.header if name in ('GetMcAction', 'GetMcPresetMotionState', 'GetJointPowerState') else request.header
        header.stamp = self.robot.node.get_clock().now().to_msg()
        return request

    def _rpc(self, name, request, *, cleanup=False, update=None):
        client = self.clients[name]
        deadline = time.monotonic() + 8
        while not client.service_is_ready():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError('原厂服务未就绪，未发送请求。')
        if update is not None:
            while time.monotonic() - self.last_control_at < .12:
                self.robot.spin(cancellable=not cleanup)
            self.robot.spin(0, cancellable=not cleanup)
            # 等待服务时可能出现抱起、跌倒或其他任务接管，实际写入前再核对。
            stopping_quad = update['stage'] == 'return' and self.record['motion']['profile'] == 'T1_quad'
            stepping_quad = (self.record['motion'].get('quad_route')
                             and self.state.action_info.current_action.value == 121)
            self._safe(stationary=update['stage'] != 'reset' and not stopping_quad and not stepping_quad,
                       cleanup=cleanup, fresh_power=True)
            if not self._owned():
                raise AudioError('当前任务归属未确认，未发送庆祝控制。')
            if self.state.action_info.switch_ready is not True:
                raise AudioError('当前运控尚未允许切换，未发送庆祝控制。')
            self.record['motion']['pending'] = name
            self._save()
            self.last_control_at = time.monotonic()
        if not cleanup:
            try:
                self.robot.checkpoint()
            except Cancelled:
                if update is not None:
                    # 尚未调用 call_async，明确没有发出请求；保留之前已确认的任务。
                    self.record['motion'].pop('pending', None)
                    self._save()
                raise
        future = client.call_async(request)
        while not future.done():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError(name + ' 请求超时，结果未知；记录保留，未重发。')
        full = future.result()
        response = full if name == 'GetMcAction' else full.response
        if response.header.code != 0:
            if update is not None:
                self.record['motion'].pop('pending', None)
                self._save()
            raise AudioError(name + ' 拒绝请求。')
        if update is not None:
            if response.state.reason != 0 or response.state.value not in (1, 400) or not response.task_id:
                raise AudioError(name + ' 未返回已接受的有效任务，记录保留。')
            motion = self.record['motion']
            previous = motion['task_id']
            motion.update(update, task_id=response.task_id, previous_task_id=previous,
                          submitted_at=self.last_control_at, owner_seen=False, running_seen=False,
                          completed=False)
            motion.pop('pending', None)
            self._save()
        return full if name in ('GetMcAction', 'GetMcMotions') else response

    def _action(self, cleanup=False):
        return self._rpc('GetMcAction', self._request('GetMcAction'), cleanup=cleanup).info

    def _require_joint_power(self, *, fresh=False, cleanup=False):
        if not fresh and time.monotonic() - self.power_checked_at < .5:
            return
        # 查询开始即废弃旧 ON；失败、超时或 UNKNOWN 不能沿用上次成功结果。
        self.power_checked_at = float('-inf')
        kind = self.types['GetJointPowerState']
        response_type = getattr(kind, 'Response', None)
        if (getattr(response_type, 'POWER_ON', None) != 1
                or getattr(response_type, 'POWER_OFF', None) != 2):
            raise AudioError('关节供电查询枚举未匹配，未发送身体动作。')
        try:
            request = self._request('GetJointPowerState')
        except AttributeError:
            raise AudioError('关节供电查询请求结构未匹配，未发送身体动作。') from None
        client = self.clients['GetJointPowerState']
        deadline = time.monotonic() + 3
        while not client.service_is_ready():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError('原厂服务未就绪，未发送请求。')
        if not cleanup:
            self.robot.checkpoint()
        future = client.call_async(request)
        while not future.done():
            self.robot.spin(cancellable=not cleanup)
            if time.monotonic() >= deadline:
                raise AudioError('关节供电查询超时，未发送身体动作。')
        result = future.result()
        # 已部署 CommonResponse 的容器名可能不同，必须找到唯一完整响应结构。
        responses = [getattr(result, name, None) for name in ('response', 'header')]
        responses = [value for value in responses if hasattr(getattr(value, 'header', None), 'code')
                     and hasattr(getattr(value, 'status', None), 'value')
                     and hasattr(value.status, 'reason')]
        if len(responses) != 1:
            raise AudioError('关节供电查询响应结构未匹配，未发送身体动作。')
        response = responses[0]
        if response.header.code != 0 or response.status.value != 1 or response.status.reason != 0:
            raise AudioError('关节供电查询未成功，未发送身体动作。')
        power = getattr(result, 'power_state', None)
        if type(power) is not int or power != response_type.POWER_ON:
            raise AudioError('关节供电未明确为 ON，未发送身体动作。')
        self.power_checked_at = time.monotonic()

    def _safe(self, *, stationary=False, cleanup=False, fresh_power=False, transition=False):
        info = self._action(cleanup)
        try:
            self._require_joint_power(fresh=fresh_power, cleanup=cleanup)
        except (AudioError, Cancelled):
            raise
        except Exception:
            raise AudioError('关节供电查询失败，未发送身体动作。') from None
        state = self.state
        now = time.monotonic()
        if (type(self.battery_status) is not int or not 0 <= self.battery_status <= 0xffffffff
                or now - self.battery_received_at > 2):
            raise AudioError('充电状态未知或反馈过期，未发送身体动作。')
        if self.battery_status & CHARGING_MASK:
            raise AudioError('正在充电或充电器已接入，未发送身体动作。')
        state_action = getattr(getattr(getattr(state, 'action_info', None), 'current_action', None), 'value', None)
        state_status = getattr(getattr(getattr(state, 'action_info', None), 'status', None), 'value', None)
        motion = self.record.get('motion')
        standing_transition = transition and motion and motion['stage'] == 'standing'
        # 已接受任务的有界等待可观察未知过渡值；提交控制和确认完成仍必须是RUNNING=100。
        if (state is None or now - self.received_at > 2 or getattr(state, 'is_fallen', None) is not False
                or type(getattr(state, 'is_moving', None)) is not bool
                or stationary and state.is_moving
                or not transition and (info.status.value != 100 or state_status != 100)
                or not transition and state_action != info.current_action.value):
            raise AudioError('运控反馈过期、跌倒、模式不一致或未确认静止；未继续庆祝。')
        for name, field in (('lift_state', 'is_lifted'), ('held_state', 'is_held')):
            extra = getattr(state, name, None)
            if extra is None or getattr(extra, field, None) is not False:
                raise AudioError('未确认机器人没有被提起或抱持，未继续庆祝。')
        if motion:
            profile = self._profile(motion)
            allowed = profile['initial'] | {profile['mode']}
            route = motion.get('quad_route')
            if route:
                allowed = {motion['initial_action'], *route['enter'], *route['return']}
                if motion.get('action') == 114:
                    # 当前原厂开心任务可自行结束到102；仍须核对同一任务归属。
                    allowed.add(102)
            if motion['stage'] in ('prepared_stand', 'standing'):
                allowed = {motion['entry_action'], motion['initial_action']}
                if standing_transition:
                    # 高层站立任务自行经过恢复/准备/踏步；这里只观察，不向中间模式发控制。
                    allowed |= {100, 115, 121}
            if info.current_action.value not in allowed or state_action not in allowed:
                raise AudioError('机器人已进入其他模式，未覆盖外部动作。')
            form = getattr(getattr(state, 'form_state', None), 'current_form', None)
            if 'form' in profile and form != profile['form']:
                raise AudioError('机器人形态已改变，未自动变形或恢复。')
        return info

    def _owned(self):
        motion = self.record['motion']
        if self.state is None or time.monotonic() - self.received_at > 2:
            return False
        if self.received_at < motion.get('submitted_at', 0):
            return False
        task_id = getattr(getattr(self.state, 'task_info', None), 'task_id', None)
        if task_id == motion['task_id']:
            if not motion.get('owner_seen'):
                motion['owner_seen'] = True
                self._save()
            return True
        if motion.get('owner_seen') or task_id != motion.get('previous_task_id'):
            raise AudioError('庆祝任务已被其他任务接管，未覆盖外部控制。')
        return False

    def _mode(self, action, stage, cleanup=False):
        request = self._request('SetMcAction')
        request.source = 'sdk_rpc'
        request.command.action.value, request.command.action_desc = action, ACTION_NAMES[action]
        self._rpc('SetMcAction', request, cleanup=cleanup, update={'stage': stage, 'action': action})
        self._wait_mode(action, cleanup)

    def _wait_mode(self, action, cleanup=False):
        deadline = time.monotonic() + (25 if self.record['motion']['stage'] == 'standing' else 8)
        while True:
            self.robot.spin(cancellable=not cleanup)
            info = self._safe(cleanup=cleanup, transition=True)
            state_info = self.state.action_info
            natural_end = (action == 114 and self.record['motion'].get('quad_route')
                           and (cleanup or self.record['motion'].get('running_seen'))
                           and info.current_action.value == 102 and state_info.current_action.value == 102
                           and not self.state.is_moving)
            if (self._owned() and (natural_end or info.current_action.value == action
                                  and state_info.current_action.value == action)
                    and info.status.value == 100 and state_info.status.value == 100
                    and state_info.switch_ready is True
                    and (action in (114, 121) or not self.state.is_moving)):
                return
            if time.monotonic() >= deadline:
                raise AudioError('未确认庆祝模式完成切换，记录保留。')

    def _preset(self, value, stage, cleanup=False):
        request = self._request('SetMcPresetMotion')
        request.source, request.motion.value = 'sdk_rpc', value
        request.area.value, request.interrupt, request.ani_path = 0, cleanup, ''
        self._rpc('SetMcPresetMotion', request, cleanup=cleanup, update={'stage': stage, 'preset': value})

    def _wait_preset(self, cleanup=False):
        deadline = time.monotonic() + self._seconds('max_motion_seconds', 10, 1, 15)
        while True:
            self.robot.spin(cancellable=not cleanup)
            info = self._safe(cleanup=cleanup)
            motion = self.record['motion']
            request = self._request('GetMcPresetMotionState')
            request.request.task_id = motion['task_id']
            response = self._rpc('GetMcPresetMotionState', request, cleanup=cleanup)
            if response.state.reason != 0 or response.task_id not in (0, motion['task_id']):
                raise AudioError('预设动作反馈任务或状态异常，记录保留。')
            if self._owned():
                if response.state.value == 400 and not motion.get('running_seen'):
                    motion['running_seen'] = True
                    self._save()
                elif response.state.value == 1:
                    # Q1 默认姿态会直接完成并回到行走模式，已验证该反馈分支。
                    reset_done = (motion['profile'] == 'Q1' and motion['stage'] == 'reset'
                                  and info.current_action.value == 1002)
                    if (motion.get('running_seen') or reset_done) and not self.state.is_moving:
                        motion['completed'] = True
                        self._save()
                        return
                elif response.state.value != 400:
                    raise AudioError('预设动作未正常运行或完成，记录保留。')
            if time.monotonic() >= deadline:
                raise AudioError('未确认庆祝动作运行后完成，记录保留。')

    def _mimic_request(self, profile):
        self._initialize_mimic()
        request = self._request('SetMcMotion')
        # SDK 存在两版字段；本路径只接受完整、已核对的执行语义，不猜缺失字段。
        required = ('source', 'type', 'motion', 'model', 'resource', 'interrupt', 'operation')
        if (any(not hasattr(request, field) for field in required)
                or getattr(request, 'MIMIC_QY', None) != 1
                or getattr(request, 'EXECUTION', None) != 2):
            raise AudioError('SetMcMotion 请求字段或执行枚举未匹配，四足难过动作不可用。')
        request.source, request.type, request.operation = 'sdk_rpc', request.MIMIC_QY, request.EXECUTION
        request.motion, request.model, request.resource, request.interrupt = profile['motion'], '', '', False
        return request

    def _mimic(self, profile):
        self._rpc('SetMcMotion', self._mimic_request(profile), update={'stage': 'mimic'})
        deadline = time.monotonic() + self._seconds('max_motion_seconds', 10, 1, 15)
        while time.monotonic() < deadline:
            self.robot.spin()
            info = self._safe()
            motion = self.record['motion']
            if not self._owned():
                continue
            if info.current_action.value == profile['mode']:
                if not motion.get('running_seen'):
                    motion['running_seen'] = True
                    self._save()
            elif motion.get('running_seen') and not self.state.is_moving:
                # 只能在看到自有 Mimic 运行后，接受同任务返回本形态的站立。
                motion['completed'] = True
                self._save()
                return
        raise AudioError('未确认四足难过动作运行后完成，停止自有动作并保留必要恢复记录。')

    def _seconds(self, key, default, minimum, maximum):
        try:
            value = float(self.config.get(key, default))
            if not math.isfinite(value):
                raise ValueError()
            return min(max(value, minimum), maximum)
        except (TypeError, ValueError):
            raise AudioError('庆祝时长配置无效。') from None

    def _delay(self, seconds, *, motion=False):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.robot.spin()
            if motion:
                self._safe()
                if not self._owned():
                    raise AudioError('庆祝动作归属未确认。')

    def _check_stand_permission(self, mood):
        identity = {'series': self.device.series, 'sku': self.device.sku}
        verified = self.config.get('verified_sad' if mood == 'sad' else 'verified', {})
        verified_stand = self.config.get('verified_stand', {})
        if not (self.config.get('motion_enabled') is True and self.config.get('commissioned') is True
                and self.config.get('commissioned_for') == identity
                and isinstance(verified, dict) and verified.get('T1_quad') is True):
            raise AudioError('当前姿态不是已确认的稳定站立；站立准备尚未启用并完成现场验收。')
        if not isinstance(verified_stand, dict) or verified_stand.get('T1_quad') is not True:
            raise AudioError('四足坐卧到站立准备尚未完成独立现场验收，未自动起身。')

    def prepare_stand(self, mood='happy', *, before_stand=None):
        """保留站立形态，或在开局提示播完后请求原厂完成四足站立。"""
        self._initialize(motion=True)
        deadline = time.monotonic() + 6
        while self.state is None or self.battery_status is None:
            self.robot.spin()
            if time.monotonic() >= deadline:
                raise AudioError('未取得运控和充电状态，未发送站立或反馈动作。')
        info = self._safe(stationary=True)
        form = getattr(getattr(self.state, 'form_state', None), 'current_form', None)
        for name, profile in PROFILES.items():
            if (name.startswith(self.device.series) and info.current_action.value in profile['initial']
                    and ('form' not in profile or form == profile['form'])):
                return name, info
        # SDK 的高层站立请求负责内部过渡；PASSIVE 不证明坐卧姿态，始终拒绝。
        if self.device.series != 'T1' or form != 1 or info.current_action.value not in (110, 111):
            raise AudioError('当前非站立姿态没有已核对的原厂站立转换，未自动起身或变形。')
        self._check_stand_permission(mood)
        from aimdk_msgs.msg import McAction
        entry_enum = 'QUADRUPED_GET_DOWN_DEFAULT' if info.current_action.value == 110 else 'QUADRUPED_SIT_DOWN_DEFAULT'
        if (getattr(McAction, entry_enum, None) != info.current_action.value
                or getattr(McAction, 'QUADRUPED_STAND_DEFAULT', None) != 101):
            raise AudioError('原厂消息包的四足坐卧/站立枚举未匹配，未发送站立准备。')
        task_id = getattr(getattr(self.state, 'task_info', None), 'task_id', None)
        if type(task_id) is not int or task_id < 0:
            raise AudioError('缺少当前任务归属，未发送站立准备。')
        if not callable(before_stand):
            raise AudioError('仅在开局完成起身提示后准备站立；未自动起身。')
        self.record = {'version': 1, 'series': self.device.series, 'sku': self.device.sku,
                       'motion': {'profile': 'T1_quad', 'initial_action': 101,
                                  'entry_action': info.current_action.value, 'task_id': task_id,
                                  'owner_seen': True, 'stage': 'prepared_stand', 'boot_id': self._boot_id()}}
        self._save()
        try:
            before_stand()
        except Cancelled:
            raise
        except Exception as error:
            # 播放结果未知也不能继续开局或在没听完提示时起身。
            raise SetupError('起身提示未确认播完，未发送站立请求。') from error
        # _mode 在实际提交前重新核对供电、姿态、充电与提示前的任务归属。
        self._mode(101, 'standing')
        self._confirm_stand()
        self.record.pop('motion')
        self._save()
        self.record = {'version': 1, 'series': self.device.series, 'sku': self.device.sku}
        return 'T1_quad', self._safe(stationary=True)

    def _confirm_stand(self):
        # 服务接受不是站稳；至少观察半秒连续的新鲜同任务静止反馈。
        stable_at = None
        last_sequence = -1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.robot.spin(cancellable=False)
            info = self._safe(cleanup=True, transition=True)
            state_info = self.state.action_info
            if (self._owned() and info.current_action.value == 101 and state_info.current_action.value == 101
                    and info.status.value == 100 and state_info.status.value == 100
                    and state_info.switch_ready is True and not self.state.is_moving):
                if self.state_sequence == last_sequence:
                    continue
                last_sequence = self.state_sequence
                if stable_at is None:
                    stable_at = self.received_at
                elif self.received_at - stable_at >= .5:
                    return
            else:
                stable_at = None
        raise AudioError('原厂站立准备未确认稳定，记录保留；未重复发送动作。')

    @staticmethod
    def _boot_id():
        try:
            value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if not value:
                raise OSError()
            return value
        except OSError:
            raise AudioError('无法记录本次启动标识，未发送动作。') from None

    def prepare_for_game(self, motion_enabled=None, *, before_stand=None):
        """CLI 可显式覆盖本次动作开关；未验收返回 unavailable，不推测已具备实体能力。"""
        if motion_enabled is not None:
            if type(motion_enabled) is not bool:
                raise AudioError('motion_enabled 须为布尔值。')
            self.config['motion_enabled'] = motion_enabled
        if self.path.exists():
            raise AudioError('存在未清理的动作记录，未启动新的站立准备。')
        if not self.config.get('enabled', True) or self.config.get('motion_enabled') is not True:
            return {'status': 'disabled', 'reason': '身体动作未启用。'}
        self._initialize()
        self.record = {'version': 1, 'series': self.device.series, 'sku': self.device.sku}
        try:
            self._prepare_motion(before_stand=before_stand)
        except AudioError as error:
            motion = self.record.get('motion')
            if motion and (motion.get('stage') not in ('prepared', 'prepared_stand') or motion.get('pending')):
                self.failure_reason = type(error).__name__ + ': ' + str(error)
                raise
            self.motion_status = {'status': 'unavailable', 'reason': str(error)}
        except BaseException as error:
            self.failure_reason = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            self._cleanup()
        return dict(self.motion_status)

    def _prepare_motion(self, mood='happy', *, before_stand=None):
        settings = self.config
        self.motion_status = {'status': 'disabled', 'reason': '身体动作未启用。'}
        if settings.get('motion_enabled') is not True:
            return False
        self.motion_status = {'status': 'unavailable', 'reason': '身体动作尚未完成当前型号的现场验收。'}
        identity = {'series': self.device.series, 'sku': self.device.sku}
        if not (settings.get('commissioned') is True
                and settings.get('commissioned_for') == identity):
            return False
        verified = settings.get('verified_sad' if mood == 'sad' else 'verified', {})
        if not isinstance(verified, dict):
            raise AudioError('庆祝 verified 配置须为对象。')
        profiles = SAD_PROFILES if mood == 'sad' else PROFILES
        if not any(name.startswith(self.device.series) for name in profiles):
            self.motion_status = {'status': 'unavailable', 'reason': '该型号没有已核对的难过动作协议。'}
            return False
        if not any(verified.get(name) is True for name in PROFILES if name.startswith(self.device.series)):
            if mood == 'sad':
                self.motion_status['reason'] = '难过动作尚未完成独立现场验收，未发送身体动作。'
            return False
        profile_name, info = self.prepare_stand(mood, before_stand=before_stand)
        if verified.get(profile_name) is not True:
            return False
        profile = profiles[profile_name]
        task_id = getattr(getattr(self.state, 'task_info', None), 'task_id', None)
        if task_id is None:
            raise AudioError('当前运控缺少任务归属，未发送动作。')
        from aimdk_msgs.msg import McAction, McPresetMotion
        enum = McPresetMotion if 'preset' in profile else McAction
        expected = profile.get('preset', profile['mode'])
        if getattr(enum, profile['enum'], None) != expected:
            raise AudioError('部署消息包与已配置庆祝动作不匹配。')
        if 'preset' in profile:
            if getattr(McPresetMotion, 'DEFAULT_POS', None) != 0:
                raise AudioError('默认姿态枚举未确认。')
            resources = self._rpc('GetMcMotions', self._request('GetMcMotions')).motion
            for value in (0, profile['preset']):
                item = next((item for item in resources if item.tag == str(value)), None)
                if item is None or item.type.value != 1 or item.intr_info.intr_type.value != 1:
                    raise AudioError('预设动作资源缺失或不能自由打断；未发送动作。')
        if 'motion' in profile:
            self._mimic_request(profile)
            resources = self._rpc('GetMcMotions', self._request('GetMcMotions')).motion
            item = next((item for item in resources if item.tag == profile['motion']), None)
            if item is None or item.intr_info.intr_type.value != 1:
                raise AudioError('四足难过资源缺失或不能自由打断，未发送动作。')
            resource_type = getattr(item, 'type', None)
            # 注册资源 MIMIC=2 与请求 MIMIC_QY=1 属于不同枚举；必须匹配运行时符号。
            # 本地登记证据确认 MIMIC=2，消息包没有符号时不从请求类型推导。
            if (getattr(resource_type, 'MIMIC', None) != 2
                    or type(getattr(resource_type, 'value', None)) is not int
                    or resource_type.value != resource_type.MIMIC):
                raise AudioError('四足难过注册资源类型未匹配 MIMIC，未发送动作。')
        route = None
        if profile_name == 'T1_quad' and mood == 'happy':
            verified_step = settings.get('verified_step', {})
            route = self._prepare_quad_route(info.current_action.value,
                allow_step=isinstance(verified_step, dict) and verified_step.get('T1_quad') is True)
        self.record['motion'] = {'profile': profile_name, 'initial_action': info.current_action.value,
                                 'task_id': task_id, 'owner_seen': True, 'stage': 'prepared',
                                 'boot_id': self._boot_id(), 'mood': mood}
        if route:
            self.record['motion']['quad_route'] = route
        self._save()
        self.robot.spin(0)
        self._safe(stationary=True)
        self.motion_status = {'status': 'ready', 'profile': profile_name}
        return True

    def _prepare_quad_route(self, initial, *, allow_step=False):
        if initial not in self.quad_routes:
            self.quad_routes[initial] = load_quad_route(self.path.parent, initial)
        route = self.quad_routes[initial]
        if not valid_quad_route(route, initial):
            raise AudioError('四足开心转换路径无效。')
        if 121 in route['enter'] + route['return']:
            from aimdk_msgs.msg import McAction
            if getattr(McAction, 'QUADRUPED_LOCOMOTION_STEP', None) != 121:
                raise AudioError('当前消息包的四足中间模式枚举不匹配。')
            if not allow_step:
                raise AudioError('当前固件须经过四足原地踏步模式；该路径尚未单独现场验收。')
        return route

    def _face(self, reset=False, mood='happy'):
        expression = self.record.get('expression')
        if expression and expression.get('pending'):
            raise AudioError('表情请求结果未知，记录保留，未重发。')
        if reset and self.face_reset_rejected:
            raise AudioError('本次表情复位已被明确拒绝，记录保留，未自动重发。')
        request = self.types['PlayEmotion'].Request()
        request.header.header.stamp = self.robot.node.get_clock().now().to_msg()
        request.type, request.priority, request.loop_count = 'emotion', 0, 1
        face = 'reset' if reset else mood
        if face not in FACES[self.device.series]:
            return False
        request.emotion_ids = [FACES[self.device.series][face]]
        def submitted():
            self.record['expression'] = {'pending': face}
            self._save()
        try:
            play_expression(self.robot, self.clients['PlayEmotion'], request, self.device.series,
                            cleanup=reset, before_submit=submitted)
        except RequestRejected:
            if expression:
                self.record['expression'] = expression
            else:
                self.record.pop('expression', None)
            self.face_reset_rejected = reset
            self._save()
            raise
        # 只表示原厂 API 接受请求；没有视觉完成反馈，不宣称已看到表情。
        if reset:
            self.record.pop('expression')
        else:
            self.record['expression'] = {'accepted': True}
        self._save()
        return True

    def celebrate(self, outcome='success'):
        if outcome not in OUTCOMES:
            raise AudioError('反馈类型须为 success、win、failure 或 lose。')
        mood = OUTCOMES[outcome]
        if not self.config.get('enabled', True):
            return {'outcome': outcome, 'motion': {'status': 'disabled'},
                    'expression': {'status': 'disabled'}}
        if self.path.exists():
            raise AudioError('存在未清理庆祝记录，未重复庆祝。')
        self._initialize()
        self.record = {'version': 1, 'series': self.device.series, 'sku': self.device.sku}
        self.failure_reason = ''
        self.motion_status = {'status': 'not_requested', 'reason': '普通答题仅播放表情。'}
        try:
            try:
                motion_ready = outcome in ('win', 'lose') and self._prepare_motion(mood)
            except AudioError as error:
                motion = self.record.get('motion')
                if motion and (motion.get('stage') not in ('prepared', 'prepared_stand') or motion.get('pending')):
                    raise
                # 准备阶段未发送实体控制；跳过动作仍给孩子开心表情。
                self.record.pop('motion', None)
                identity = self.record.copy()
                self._save()
                self.record = identity
                logging.getLogger(__name__).info('motion_skipped: %s', error)
                self.motion_status = {'status': 'unavailable', 'reason': str(error)}
                motion_ready = False
            face_accepted = self._face(mood=mood)
            face_started = time.monotonic()
            if motion_ready:
                motion = self.record['motion']
                profile = self._profile(motion)
                if 'motion' in profile:
                    self._mimic(profile)
                elif profile['mode'] != motion['initial_action']:
                    for action in motion.get('quad_route', {}).get('enter', [profile['mode']]):
                        self._mode(action, 'mode')
                if 'preset' in profile:
                    self._preset(profile['preset'], 'preset')
                    self._wait_preset()
                elif 'motion' not in profile:
                    self._delay(self._seconds('quad_seconds', 1.2, .3, 2), motion=True)
                # 身体先按自己的短时限归位，不能为了播完表情而延长开心运动。
                self._finish_motion()
            if face_accepted:
                # 当前 T1 的90/21均为4.004秒动画；ACK后5秒的完整播放已现场确认。
                default, minimum, maximum = (5, 5, 6) if self.device.series == 'T1' else (1.2, .3, 2)
                duration = self._seconds('expression_seconds', default, minimum, maximum)
                self._delay(max(0, face_started + duration - time.monotonic()))
        except BaseException as error:
            self.failure_reason = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            self._cleanup()
        motion_result = dict(self.motion_status)
        if motion_ready:
            motion_result['status'] = 'completed'
        expression_result = {'status': 'accepted_and_reset'} if face_accepted else {
            'status': 'unavailable', 'reason': '该型号未提供已核对的难过表情，未使用其他表情冒充。'}
        return {'outcome': outcome, 'motion': motion_result, 'expression': expression_result}

    def sad(self):
        return self.celebrate('lose')

    def _restore_motion(self):
        motion = self.record['motion']
        if motion.get('pending'):
            raise AudioError('动作请求结果未知，记录保留，未重发。')
        if motion['stage'] in ('prepared', 'prepared_stand'):
            # 尚未发送任何实体动作，无需对外部运控做恢复写入。
            self.record.pop('motion')
            self._save()
            return
        if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != motion['boot_id']:
            raise AudioError('机器人已重启，旧任务归属不能沿用，请现场确认。')
        if motion['stage'] == 'standing':
            # 取消/超时后只等待自有站立任务；不再次起身，也不把机器人重新放倒。
            self._confirm_stand()
            self.record.pop('motion')
            self._save()
            return
        route = motion.get('quad_route')
        if route:
            # 请求已接受但等待途中取消时，先确认同一任务完成切换，再走已记录的返回边。
            self._wait_mode(motion['action'], cleanup=True)
        deadline = time.monotonic() + 8
        while True:
            self.robot.spin(cancellable=False)
            if self.state is not None and time.monotonic() - self.received_at <= 2:
                self._safe(cleanup=True)
                if self._owned():
                    break
            if time.monotonic() >= deadline:
                raise AudioError('恢复前未确认自有任务，未覆盖外部动作。')
        if motion['stage'] == 'return' and not route:
            self._wait_mode(motion['initial_action'], cleanup=True)
        if motion['stage'] == 'preset':
            self._preset(0, 'reset', cleanup=True)
        if motion['stage'] == 'reset':
            self._wait_preset(cleanup=True)
        info = self._safe(stationary=motion['profile'] != 'T1_quad', cleanup=True)
        if info.current_action.value != motion['initial_action']:
            returning = ([motion['initial_action']] if not route or info.current_action.value == 121
                         else route['return'])
            for action in returning:
                self._mode(action, 'return', cleanup=True)
        self._safe(stationary=True, cleanup=True)
        if not self._owned():
            raise AudioError('未确认恢复后任务归属，记录保留。')
        self.record.pop('motion')
        self._save()

    def _cleanup(self):
        if not self.record_valid:
            return
        errors = []
        if self.record.get('motion'):
            try:
                self._finish_motion()
            except Exception as error:
                errors.append(str(error))
        if self.record.get('expression'):
            try:
                self._face(reset=True)
            except Exception as error:
                errors.append(str(error))
        if errors:
            details = '；'.join(errors)
            if self.failure_reason and self.failure_reason not in details:
                details = '原始错误：' + self.failure_reason + '；' + details
            elif not self.failure_reason:
                self.failure_reason = details
            raise AudioError('庆祝清理未确认：' + details)

    def _finish_motion(self):
        if self.motion_cleanup_error:
            raise AudioError(self.motion_cleanup_error)
        try:
            self._restore_motion()
        except Exception as error:
            # 提前归位失败后 finally/close 不再自动尝试第二次身体恢复。
            self.motion_cleanup_error = str(error)
            raise

    def recover(self):
        self.record_valid = False
        self.record = read_json(self.path)
        if not self.record:
            return
        self._initialize(motion=bool(self.record.get('motion')))
        if (self.record.get('version') != 1 or self.record.get('series') != self.device.series
                or self.record.get('sku') != self.device.sku):
            raise AudioError('庆祝记录身份不匹配，未执行恢复。')
        motion = self.record.get('motion')
        if motion:
            profiles = SAD_PROFILES if isinstance(motion, dict) and motion.get('mood') == 'sad' else PROFILES
            profile = profiles.get(motion.get('profile')) if isinstance(motion, dict) else None
            if (profile is None or not motion['profile'].startswith(self.device.series)
                    or motion.get('mood', 'happy') not in ('happy', 'sad')
                    or motion.get('initial_action') not in profile['initial']
                    or motion.get('stage') not in ('prepared', 'prepared_stand', 'standing', 'mode', 'preset', 'mimic', 'reset', 'return')
                    or not isinstance(motion.get('task_id'), int) or motion['task_id'] < 0
                    or not isinstance(motion.get('boot_id'), str) or not motion['boot_id']):
                raise AudioError('庆祝动作恢复记录不完整，未执行恢复。')
            if motion['stage'] == 'mimic' and 'motion' not in profile:
                raise AudioError('难过全身动作恢复记录与协议不匹配，未执行恢复。')
            if motion['stage'] in ('prepared_stand', 'standing') and (
                    motion['profile'] != 'T1_quad' or motion['initial_action'] != 101
                    or motion.get('entry_action') not in (110, 111)):
                raise AudioError('站立准备恢复记录不完整，未执行恢复。')
            if motion['profile'] == 'T1_quad' and motion.get('mood', 'happy') == 'happy' and motion['stage'] not in ('prepared_stand', 'standing'):
                route = motion.get('quad_route')
                if (not valid_quad_route(route, motion['initial_action'])
                        or route != load_quad_route(self.path.parent, motion['initial_action'])):
                    raise AudioError('四足开心恢复路径缺失或已与当前固件不同，未发送恢复动作。')
        expression = self.record.get('expression')
        if expression and (not isinstance(expression, dict) or
                           not (expression.get('accepted') is True or expression.get('pending'))):
            raise AudioError('庆祝表情恢复记录不完整，未执行恢复。')
        self.record_valid = True
        self._cleanup()

    def close(self):
        try:
            self._cleanup()
        finally:
            for subscription in self.subscriptions:
                self.robot.node.destroy_subscription(subscription)
            for client in self.clients.values():
                self.robot.node.destroy_client(client)
            self.subscriptions, self.clients = [], {}
