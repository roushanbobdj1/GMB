"""Passenger WSGI entrypoint for Hostinger Python hosting."""

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

try:
    os.chdir(APP_DIR)
except OSError:
    pass

from app import app as application
