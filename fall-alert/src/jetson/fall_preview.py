"""Mirror the current YOLO frame and compensate for screen orientation."""

import cv2
import numpy as np


def mirror_preview(image, detections, fallen):
    """Mirror camera pixels and box coordinates, then draw readable labels."""
    preview = cv2.flip(image, 1)
    height, width = preview.shape[:2]
    for (x, y, box_width, box_height), score in detections:
        left = max(0, width - 1 - (x + box_width))
        right = min(width - 1, width - 1 - x)
        top = max(0, y)
        bottom = min(height - 1, y + box_height)
        if left > right or top > bottom:
            continue
        color = (30, 50, 255) if [x, y, box_width, box_height] in fallen else (0, 255, 80)
        cv2.rectangle(preview, (left, top), (right, bottom), color, 4)
        cv2.putText(preview, f'PERSON {score:.2f}', (max(1, left), max(19, top-5)),
                    cv2.FONT_HERSHEY_SIMPLEX, .58, color, 2)
    return preview


def screen_frame(image, detections, fallen, pose, badges, label, number, rate):
    preview = mirror_preview(image, detections, fallen)
    canvas = np.zeros((272, 480, 3), np.uint8)
    canvas[:, 58:421] = cv2.resize(preview, (363, 272))
    cv2.putText(canvas, 'H264 LIVE  ' + pose.upper(), (4, 14),
                cv2.FONT_HERSHEY_SIMPLEX, .38, (20, 240, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, f'{number:04d}  {len(detections)} person  {rate:.1f} fps', (5, 267),
                cv2.FONT_HERSHEY_SIMPLEX, .43, (40, 255, 255), 1, cv2.LINE_AA)
    if badges is not None:
        canvas[3:31, 335:477] = badges[label*28:(label+1)*28]
    if pose == 'quadruped':
        canvas = cv2.rotate(canvas, cv2.ROTATE_180)
    return canvas
