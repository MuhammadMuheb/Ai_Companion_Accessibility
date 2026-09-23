"""Kept for old "Start with Windows" shortcuts: starts Nova quietly in the tray (same as
`nova.pyw --background`). New shortcuts point to nova.pyw."""

import os
import runpy
import sys

sys.argv = [sys.argv[0]]  # Nova starts invisibly by default
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova.pyw"), run_name="__main__")
