"""Finish installation inside the Jetson venv after sourcing the factory ROS environment."""
import ctypes
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/jetson'))

from settings import imported_yaml, parse, read_private, write_private


def prepare(config, legacy=None):
    from yaml import safe_load
    template = (ROOT / 'cfg/config.example.yaml').read_text()
    text = read_private(config)
    if legacy is not None:
        if text.strip() != template.strip():
            raise ValueError('配置文件已有修改，不能导入旧配置。')
        text = imported_yaml(read_private(legacy), template)
    configured = text.strip() != template.strip()
    settings = parse(text) if configured else safe_load(template)

    # These imports exercise the native ABI used by inference and by the voice interface.
    import cv2, numpy, rclpy, tensorrt
    from aimdk_msgs.srv import BusinessAgentControl, CaptureJpegImage, PlayTts
    ctypes.CDLL('libcudart.so.12')
    if settings['sms']['provider'] == 'aliyun':
        subprocess.run([sys.executable, '-m', 'pip', '--disable-pip-version-check', 'install',
                        '-r', str(ROOT / 'scripts/requirements-aliyun.txt')], check=True)
        from alibabacloud_dysmsapi20170525.client import Client
        from alibabacloud_dysmsapi20170525.models import SendSmsRequest
        from alibabacloud_tea_openapi.models import Config
        from alibabacloud_tea_util.models import RuntimeOptions
    if configured:
        from sms_sender import sender_for
        sender_for(settings)
    if legacy is not None:
        write_private(config, text)


if __name__ == '__main__':
    try:
        prepare(*(Path(arg) for arg in sys.argv[1:]))
    except (ImportError, OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f'Jetson 安装失败，请查看 docs/installation.md。{error}')
