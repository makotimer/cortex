# Re-export run(**kwargs) for runner/importlib convenience.
# evaluate()/main_cli() are the machine-readable path the ops nightly uses.
from .lib import logging_bridge
from .main import evaluate, main_cli, run

__all__ = ["evaluate", "logging_bridge", "main_cli", "run"]
