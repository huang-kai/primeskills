"""Stationary full-frame lying-person detection and event rearming."""

import json

import cv2
import numpy as np


# Normalized camera coordinates, measured in the unmirrored test view.
FLOOR = np.array([(0.405, 0.70), (0.635, 0.70), (0.665, 0.795),
                  (0.775, 0.99), (0.345, 0.99), (0.385, 0.81)], np.float32)
QUADRUPED_FLOOR = np.array([(0.414, 0.575), (0.673, 0.575), (0.715, 0.66),
                            (0.846, 0.98), (0.18, 0.98), (0.385, 0.67)], np.float32)


def lying_mask(mask, floor_polygon=None, *, low_view=False):
    """Use the central portion of the silhouette so limbs have less influence."""
    y, x = np.nonzero(mask)
    if len(x) < 18:
        return False
    left, right = np.percentile(x, [10, 90])
    top, bottom = np.percentile(y, [10, 90])
    # In the lower quadruped view the body can project above the back of the mat.
    if floor_polygon is not None:  # Older offline recordings may specify a calibrated region.
        point = (float(np.median(x)), float(bottom if low_view else np.median(y)))
        if cv2.pointPolygonTest(floor_polygon, point, False) < 0:
            return False
    return right - left >= 1.35 * max(2.0, bottom - top)


def on_furniture(box, surfaces):
    """Reject a body largely inside a detected seat, bed or television box."""
    x, y, width, height = box
    for sx, sy, sw, sh in surfaces:
        common_w = max(0, min(x + width, sx + sw) - max(x, sx))
        common_h = max(0, min(y + height, sy + sh) - max(y, sy))
        if y + height <= sy + sh and common_w * common_h >= .6 * width * height:
            return True
    return False


def overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    common_w = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    common_h = max(0, min(ay + ah, by + bh) - max(ay, by))
    common = common_w * common_h
    return common / max(1, aw * ah + bw * bh - common)


class FloorTest:
    def __init__(self, hold_seconds=2.0):
        self.hold_seconds = hold_seconds
        self.previous = []

    def update(self, now, candidates, others=()):
        # One detection can continue only one previous person's timer.
        previous = [(box, start, last, evidence) for box, start, last, evidence in self.previous
                    if 0 < now - last <= 1.0 and not any(overlap(box, other) >= .35 for other in others)]
        matches = sorted(((overlap(box, old), i, j)
                          for i, box in enumerate(candidates)
                          for j, (old, _, _, _) in enumerate(previous)), reverse=True)
        starts = [now] * len(candidates)
        evidence = [0.0] * len(candidates)
        used_new, used_old = set(), set()
        for iou, i, j in matches:
            if iou < 0.35:
                break
            if i not in used_new and j not in used_old:
                _, starts[i], last, prior_evidence = previous[j]
                evidence[i] = prior_evidence + min(now - last, 0.20)
                used_new.add(i)
                used_old.add(j)
        observed = [(box, start, now, hits) for box, start, hits in zip(candidates, starts, evidence)]
        self.previous = observed + [track for j, track in enumerate(previous) if j not in used_old]
        return [box for box, start, _, hits in observed if now - start >= self.hold_seconds and hits >= 0.80]


class FallAnnouncement:
    """One announcement until the person is seen upright at the incident location."""
    def __init__(self, notifier=None):
        self.notifier = notifier
        active = notifier.active() if notifier else None
        self.floor_box = json.loads(active['box']) if active else None
        self.upright_since = None
        self.last_upright = None

    def update(self, now, fallen, candidates, people):
        if self.floor_box is None:
            if not fallen:
                return False
            self.floor_box = fallen[0]
            if self.notifier:
                self.notifier.begin(self.floor_box)
            return True
        if fallen:
            # A stance change can move this same participant in the camera image.
            self.floor_box = fallen[0]
        fx, fy, fw, fh = self.floor_box
        same_place_upright = any(
            h >= 1.5 * w and fx - fw * .25 <= x + w / 2 <= fx + fw * 1.25
            and abs((y + h) - (fy + fh)) <= fh
            for x, y, w, h in people
        )
        if candidates or not same_place_upright:
            self.upright_since = self.last_upright = None
        else:
            if self.last_upright is None or now - self.last_upright > .65:
                self.upright_since = now
            self.last_upright = now
            if now - self.upright_since >= 1.2:
                self.floor_box = self.upright_since = self.last_upright = None
                if self.notifier:
                    self.notifier.recovered_upright()
        return False
