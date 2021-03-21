import signal
from typing import Union
import sys
import signal
import subprocess
import json
import traceback
import platform
import time
from pathlib import Path

import pytest
import numpy as np
import cv2
import imageio

import pyvirtualcam
from pyvirtualcam import PixelFormat

if platform.system() == 'Windows':
    import pyvirtualcam_win_dshow_capture as dshow

    def capture_rgb(device: str, width: int, height: int) -> np.ndarray:
        return dshow.capture(device, width, height)

elif platform.system() in ['Linux', 'Darwin']:
    def capture_rgb(device: Union[str, int], width: int, height: int) -> np.ndarray:
        print(f'Opening device {device} for capture')
        vc = cv2.VideoCapture(device)
        assert vc.isOpened()
        print(f'Configuring resolution of device {device}')
        vc.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        vc.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        print(f'Reading frame from device {device}')
        for _ in range(50):
            ret, frame = vc.read()
        assert ret
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return rgb

w = 1280
h = 720
fps = 20

def get_test_frame(w, h, fmt: PixelFormat):
    def rgb_color(r, g, b):
        if fmt == PixelFormat.RGB:
            return np.array([r, g, b], np.uint8).reshape(1,1,3)
        elif fmt == PixelFormat.BGR:
            return np.array([b, g, r], np.uint8).reshape(1,1,3)
    
    if fmt in [PixelFormat.RGB, PixelFormat.BGR]:
        frame = np.empty((h, w, 3), np.uint8)
        frame[:h//2,:w//2] = rgb_color(220, 20, 60)
        frame[:h//2,w//2:] = rgb_color(240, 230, 140)
        frame[h//2:,:w//2] = rgb_color(50, 205, 50)
        frame[h//2:,w//2:] = rgb_color(238, 130, 238)
    elif fmt == PixelFormat.GRAY:
        frame = np.empty((h, w), np.uint8)
        frame[:h//2,:w//2] = 30
        frame[:h//2,w//2:] = 60
        frame[h//2:,:w//2] = 150
        frame[h//2:,w//2:] = 230
    elif fmt == PixelFormat.I420:
        frame = np.empty((h + h // 2, w), np.uint8)
        # Y plane
        frame[:h//2,:w//2] = 30
        frame[:h//2,w//2:] = 60
        frame[h//2:h,:w//2] = 100
        frame[h//2:h,w//2:] = 200
        # UV planes
        s = h // 4
        u = h
        v = h + s
        frame[u:u+s//2] = 100
        frame[u+s//2:v] = 10
        frame[v:v+s//2] = 30
        frame[v+s//2:] = 200
    elif fmt == PixelFormat.NV12:
        frame = get_test_frame(w, h, PixelFormat.I420)
        # UUVV -> UVUV
        u = frame[h:h + h // 4].copy()
        v = frame[h + h // 4:].copy()
        uv = frame[h:].reshape(-1)
        uv[::2] = u.reshape(-1)
        uv[1::2] = v.reshape(-1)
    elif fmt in [PixelFormat.YUYV, PixelFormat.UYVY]:
        frame = np.empty((h, w, 2), np.uint8)
        if fmt == PixelFormat.YUYV:
            y = frame.reshape(-1)[::2].reshape(h, w)
            u = frame.reshape(-1)[1::4].reshape(h, w // 2)
            v = frame.reshape(-1)[3::4].reshape(h, w // 2)
        elif fmt == PixelFormat.UYVY:
            y = frame.reshape(-1)[1::2].reshape(h, w)
            u = frame.reshape(-1)[::4].reshape(h, w // 2)
            v = frame.reshape(-1)[2::4].reshape(h, w // 2)
        else:
            assert False
        y[:h//2,:w//2] = 30
        y[:h//2,w//2:] = 60
        y[h//2:h,:w//2] = 100
        y[h//2:h,w//2:] = 200
        u[:h//2] = 100
        u[h//2:] = 10
        v[:h//2] = 30
        v[h//2:] = 200
    else:
        assert False
    return frame

formats = [
    PixelFormat.RGB,
    PixelFormat.BGR,
    PixelFormat.GRAY,
    PixelFormat.I420,
    PixelFormat.NV12,
    PixelFormat.YUYV,
    PixelFormat.UYVY,
]

frames = { fmt: get_test_frame(w, h, fmt) for fmt in formats }

frames_rgb = {
    PixelFormat.RGB: frames[PixelFormat.RGB],
    PixelFormat.BGR: cv2.cvtColor(frames[PixelFormat.BGR], cv2.COLOR_BGR2RGB),
    PixelFormat.GRAY: cv2.cvtColor(frames[PixelFormat.GRAY], cv2.COLOR_GRAY2RGB),
    PixelFormat.I420: cv2.cvtColor(frames[PixelFormat.I420], cv2.COLOR_YUV2RGB_I420),
    PixelFormat.NV12: cv2.cvtColor(frames[PixelFormat.NV12], cv2.COLOR_YUV2RGB_NV12),
    PixelFormat.YUYV: cv2.cvtColor(frames[PixelFormat.YUYV], cv2.COLOR_YUV2RGB_YUYV),
    PixelFormat.UYVY: cv2.cvtColor(frames[PixelFormat.UYVY], cv2.COLOR_YUV2RGB_UYVY),
}

@pytest.mark.parametrize("fmt", formats)
def test_capture(fmt, tmp_path):
    if fmt == PixelFormat.NV12 and platform.system() == 'Linux':
        pytest.skip('OpenCV VideoCapture does not support NV12')

    # informational only
    imageio.imwrite(f'test_{fmt}_in.png', frames_rgb[fmt])

    # Sending frames via pyvirtualcam and capturing them in parallel via OpenCV / DShow
    # is done in separate processes to avoid locking and cleanup issues.
    info_path = tmp_path / 'info.json'
    p = subprocess.Popen([
        sys.executable, __file__,
        '--mode', 'send',
        '--fmt', str(fmt),
        '--info-path', str(info_path)])
    try:
        # wait for subprocess to start up and start sending frames
        time.sleep(5)
        alive = p.poll() is None
        assert alive
        
        subprocess.run([
            sys.executable, __file__,
            '--mode', 'capture',
            '--fmt', str(fmt),
            '--info-path', str(info_path)],
            check=True)
    finally:
        p.terminate()
        exitcode = p.wait()
        if platform.system() == 'Windows':
            assert exitcode == 1
        else:
            assert exitcode == -signal.SIGTERM

    captured_rgb = imageio.imread(get_capture_filename(fmt))
    d = np.fabs(captured_rgb.astype(np.int16) - frames_rgb[fmt]).max()
    assert d <= 2

def get_capture_filename(fmt):
    pyver = f'{sys.version_info.major}{sys.version_info.minor}'
    return f'test_{fmt}_out_{platform.system()}_py{pyver}.png'

def send_frames(fmt: PixelFormat, info_path: Path):
    frame = frames[fmt]
    try:
        with pyvirtualcam.Camera(w, h, fps, fmt=fmt) as cam:
            print(f'sending frames to {cam.device}...')
            with open(info_path, 'w') as f:
                json.dump(cam.device, f)
            while True:
                cam.send(frame)
                cam.sleep_until_next_frame()
    except:
        traceback.print_exc()
        sys.exit(2)

def capture_frame(fmt, info_path: Path):
    if platform.system() == 'Darwin':
        device = 0
    else:
        with open(info_path) as f:
            device = json.load(f)
    captured_rgb = capture_rgb(device, w, h)
    imageio.imwrite(get_capture_filename(fmt), captured_rgb)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['send', 'capture'], required=True)
    parser.add_argument('--fmt', type=lambda fmt: PixelFormat[fmt], required=True)
    parser.add_argument('--info-path', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'send':
        send_frames(args.fmt, args.info_path)
    else:
        capture_frame(args.fmt, args.info_path)
    