#!/usr/bin/env python3
"""本地校验与 Console 配置入口；真实业务使用 PrimeBuddy 原生工具适配。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
# 安装后位于 ~/.primebuddy/skills；开发时框架与技能仓库相邻。
CANDIDATES = [ROOT.parents[1], *(ROOT.parents[1] / name for name in ('PrimeBuddy', 'primebuddy'))]
FRAMEWORK = next((path for path in CANDIDATES if (path / 'src/primebuddy').is_dir()), CANDIDATES[1])
sys.path[:0] = [str(FRAMEWORK / 'src'), str(FRAMEWORK / '_deps')]

if __name__ == '__main__':
    from primebuddy.tools.check_skill import main
    raise SystemExit(main(ROOT))
