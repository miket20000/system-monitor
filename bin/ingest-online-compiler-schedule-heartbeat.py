#!/usr/bin/env python3
import sys

sys.path.insert(0, "/usr/local/lib/system-monitor")

from system_monitor.heartbeat_receiver import main

raise SystemExit(main())
