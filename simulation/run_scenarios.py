#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sim_env.scenarios import main
if __name__ == '__main__':
    main()
