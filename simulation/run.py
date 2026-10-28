#!/usr/bin/env python3
"""Run from any directory; use this folder's isolated Python environment."""
import os
from pathlib import Path
import sys
os.environ.setdefault('MUJOCO_GL', 'egl' if sys.platform.startswith('linux') else 'glfw')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sim_env.server import main
if __name__ == '__main__':
    main()
