"""Read standing posture from native motion state."""

BIPED = {"BIPED_STAND_DEFAULT", "BIPED_LOCOMOTION_WBC"}
QUADRUPED = {"QUADRUPED_STAND_DEFAULT", "QUADRUPED_LOCOMOTION_DEFAULT"}
REST = {"PASSIVE_DEFAULT", "QUADRUPED_GET_DOWN_DEFAULT"}


def stance(message):
    info = message.action_info
    if (message.is_moving or message.is_fallen or message.lift_state.is_lifted
            or info.status.value != 100):
        return None
    form = message.form_state.current_form
    fsm = message.fsm_state.current_state
    if info.action_desc in BIPED and (form, fsm) == (2, 2):
        return "biped"
    if info.action_desc in QUADRUPED and (form, fsm) == (1, 2):
        return "quadruped"
    if info.action_desc in REST and form == 1 and info.switch_ready:
        return "rest"
    return None

class StandingView:
    """Reject captures spanning motion, a posture change or a break in telemetry."""
    def __init__(self, target, follow_stance, tracking):
        self.target = target
        self.follow_stance = follow_stance
        self.tracking = tracking
        self.last_state = self.last_bad = 0.
        self.stable_since = None
        self.action = None

    def update(self, msg, now):
        pose = stance(msg)
        changed = self.follow_stance and pose in ('biped', 'quadruped') and pose != self.target
        if changed:
            self.target = pose
        if changed or now - self.last_state >= .45 or pose != self.target:
            self.last_bad = now
            self.stable_since = None
            if self.tracking:
                self.tracking.previous.clear()
        if pose == self.target and self.stable_since is None:
            self.stable_since = now
        self.last_state = now
        self.action = msg.action_info.action_desc

    def stable(self, now):
        return (self.stable_since is not None and now - self.stable_since >= .5
                and now - self.last_state < .45)
