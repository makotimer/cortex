# Keep this TINY so importing the package never drags in heavy deps.
from . import lib  # so: from modules.bible_plan import lib
from .main import run  # so: from modules.bible_plan import run

__all__ = ["lib", "run"]
