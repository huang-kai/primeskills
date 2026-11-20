"""YOLO26n person boxes and lying silhouettes on the Jetson TensorRT engine."""

import ctypes as ct
from pathlib import Path

import cv2
import numpy as np
import tensorrt as trt

from floor_detection import lying_mask, on_furniture

MODEL = Path('/robot/software/algorithm/models/yolo26n-seg_640x480_fp16_nx.engine')


def check_cuda(status):
    if status:
        raise RuntimeError(f'CUDA status {status}')


class PersonDetector:
    def __init__(self, detect_floor):
        self.detect_floor = detect_floor
        self.cuda = ct.CDLL('libcudart.so.12')
        self.logger = trt.Logger(trt.Logger.ERROR)
        self.runtime = trt.Runtime(self.logger)
        self.engine = self.runtime.deserialize_cuda_engine(MODEL.read_bytes())
        self.context = self.engine.create_execution_context()
        self.gpu = {}
        self.stream = ct.c_void_p()
        self.pred = np.empty((116, 6300), np.float32)
        self.prototypes = np.empty((32, 120, 160), np.float32)
        expected = {'images': (1, 3, 480, 640), 'output0': (1, *self.pred.shape),
                    'output1': (1, *self.prototypes.shape)}
        try:
            for name, shape in expected.items():
                dtype = np.dtype(trt.nptype(self.engine.get_tensor_dtype(name)))
                if tuple(self.engine.get_tensor_shape(name)) != shape or dtype != np.float32:
                    raise ValueError(f'Unexpected YOLO tensor layout for {name}')
                ptr = ct.c_void_p()
                size = int(np.prod(shape)) * dtype.itemsize
                check_cuda(self.cuda.cudaMalloc(ct.byref(ptr), ct.c_size_t(size)))
                self.gpu[name] = ptr
                if not self.context.set_tensor_address(name, ptr.value):
                    raise RuntimeError(f'Cannot bind YOLO tensor {name}')
            check_cuda(self.cuda.cudaStreamCreate(ct.byref(self.stream)))
        except BaseException:
            self.close()
            raise

    def close(self):
        for ptr in self.gpu.values():
            self.cuda.cudaFree(ptr)
        self.gpu.clear()
        if self.stream.value:
            self.cuda.cudaStreamDestroy(self.stream)
            self.stream = ct.c_void_p()

    def download(self, name, target):
        check_cuda(self.cuda.cudaMemcpy(ct.c_void_p(target.ctypes.data), self.gpu[name],
                                        ct.c_size_t(target.nbytes), 2))

    def infer(self, image):
        height, width = image.shape[:2]
        scale = min(640 / width, 480 / height)
        w, h = round(width * scale), round(height * scale)
        dx, dy = (640 - w) // 2, (480 - h) // 2
        rgb = np.full((480, 640, 3), 114, np.uint8)
        rgb[dy:dy+h, dx:dx+w] = cv2.cvtColor(cv2.resize(image, (w, h)), cv2.COLOR_BGR2RGB)
        pixels = np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32)
        pixels *= 1 / 255.
        check_cuda(self.cuda.cudaMemcpy(self.gpu['images'], ct.c_void_p(pixels.ctypes.data),
                                        ct.c_size_t(pixels.nbytes), 1))
        if not self.context.execute_async_v3(self.stream.value):
            raise RuntimeError('YOLO inference failed')
        check_cuda(self.cuda.cudaStreamSynchronize(self.stream))
        self.download('output0', self.pred)
        labels = self.pred[4:84].argmax(axis=0)
        confidence = self.pred[labels + 4, np.arange(len(labels))]
        def camera_box(i):
            x, y, bw, bh = map(float, self.pred[:4, i])
            return [(x - bw / 2 - dx) / scale, (y - bh / 2 - dy) / scale, bw / scale, bh / scale]

        threshold = .20 if self.detect_floor else .35
        people = np.flatnonzero((labels == 0) & (confidence >= threshold)
                                & (self.pred[2] > 0) & (self.pred[3] > 0))
        if not people.size:
            return [], []
        boxes = [[round(v) for v in camera_box(i)] for i in people]
        scores = [float(confidence[i]) for i in people]
        kept = np.asarray(cv2.dnn.NMSBoxes(boxes, scores, threshold, .45)).reshape(-1)
        detections = [(boxes[i], scores[i]) for i in kept]
        if not self.detect_floor or not detections:
            return detections, []
        furniture = [camera_box(i) for i in np.flatnonzero(
            np.isin(labels, [56, 57, 59, 62]) & (confidence >= .5))]
        self.download('output1', self.prototypes)
        candidates = []
        for i in kept:
            x, y, bw, bh = boxes[i]
            x0 = max(0, int((x * scale + dx) // 4))
            y0 = max(0, int((y * scale + dy) // 4))
            x1 = min(160, int(np.ceil(((x + bw) * scale + dx) / 4)))
            y1 = min(120, int(np.ceil(((y + bh) * scale + dy) / 4)))
            if x0 < x1 and y0 < y1:
                coeff = self.pred[84:, people[i]]
                mask = np.einsum('c,cyx->yx', coeff, self.prototypes[:, y0:y1, x0:x1]) > 0
                if lying_mask(mask) and not on_furniture(boxes[i], furniture):
                    candidates.append(boxes[i])
        return [(b, s) for b, s in detections if s >= .35 or b in candidates], candidates
