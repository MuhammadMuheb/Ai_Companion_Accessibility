"""Start Nova — double-click this file (no console window opens).

Nova starts invisibly in the background — just say "Hello Nova" (or your wake name).
Launching it again while it runs opens its window.
    nova.pyw           start invisibly (also what "Start with Windows" does)
    nova.pyw --show    start and open the window
"""

import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# pythonw has no console: send stray output to a log file instead of losing it
log_dir = os.path.join("data", "logs")
os.makedirs(log_dir, exist_ok=True)
if sys.stdout is None or not sys.stdout.isatty():
    sys.stdout = sys.stderr = open(os.path.join(log_dir, "background_console.log"), "a", encoding="utf-8", buffering=1)

from app.daemon import run  # noqa: E402

sys.exit(run(show_window="--show" in sys.argv))
