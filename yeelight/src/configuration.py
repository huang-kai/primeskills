"""易来连接默认值，用户选择仍由框架保存。"""
from pathlib import Path
from primebuddy.config.storage import read_config

DEFAULTS = read_config(Path(__file__).resolve().parents[1] / 'cfg/config.yaml', private=False)
DEFAULT_EXECUTABLE = DEFAULTS['executable']
