"""Locate bundled resources in both source and frozen application runs."""
from pathlib import Path
import sys


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def asset_path(name):
    return resource_root() / 'assets' / name
