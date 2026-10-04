#!/usr/bin/env python3
import sys
from pathlib import Path

LIB = Path.home() / ".local/lib/system-monitor"
sys.path.insert(0, str(LIB))

from system_monitor.cli import main

raise SystemExit(main(["--config", str(Path.home() / ".config/devbox-monitor/config.json"), *sys.argv[1:]]))
