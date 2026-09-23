"""Start MD — double-click this file (no console window opens).

MD starts invisibly in the background — just say "Hey MD" (or your wake name).
Launching it again while it runs opens its window.
    md.pyw           start invisibly (also what "Start with Windows" does)
    md.pyw --show    start and open the window
    md.pyw --selftest   check every component, write data/logs/selftest.json, exit
"""

import os
import sys

if getattr(sys, "frozen", False):
    # installed MD.exe: work in the writable data folder, not in Program Files
    data_home = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "MD")
    os.makedirs(data_home, exist_ok=True)
    os.chdir(data_home)
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# pythonw has no console: send stray output to a log file instead of losing it
log_dir = os.path.join("data", "logs")
os.makedirs(log_dir, exist_ok=True)
if sys.stdout is None or not sys.stdout.isatty():
    sys.stdout = sys.stderr = open(os.path.join(log_dir, "background_console.log"), "a", encoding="utf-8", buffering=1)

if "--selftest" in sys.argv:
    from app.selftest import run as selftest  # noqa: E402

    sys.exit(selftest())

from app.daemon import run  # noqa: E402

sys.exit(run(show_window="--show" in sys.argv))
