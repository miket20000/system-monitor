#!/usr/bin/env python3
import sys
from pathlib import Path

LIB = Path("/usr/local/lib/system-monitor")
sys.path.insert(0, str(LIB))

from system_monitor.cli import main

raise SystemExit(main(["--config", "/etc/gp-monitor/config.json", *sys.argv[1:]]))
