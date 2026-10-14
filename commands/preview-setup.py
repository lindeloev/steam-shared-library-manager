#!/usr/bin/python3
"""Describe proposed account setup without modifying Steam configuration."""
import os
from pathlib import Path
import sys
os.execv("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B", str(Path(__file__).with_name("account-worker.py")), "--action", "plan", *sys.argv[1:]])
