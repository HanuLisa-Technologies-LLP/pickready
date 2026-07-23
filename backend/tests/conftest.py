"""Make the backend package root importable when pytest runs from anywhere."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
