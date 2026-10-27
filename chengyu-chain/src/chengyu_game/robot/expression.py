"""表情统一使用原厂 ROS 服务；超时不重发，也不切换到 HTTP。"""
from ..support import AudioError


def play_expression(robot, client, request, series, *, cleanup=False, before_submit=None):
    if series == 'T1' and (request.type != 'emotion' or list(request.emotion_ids) not in ([13], [21], [90])
                           or request.loop_count != 1 or request.priority != 0):
        raise AudioError('T1 表情请求不在已核对范围内，未发送。')
    return robot.call(client, request, cancellable=not cleanup, before_submit=before_submit)
