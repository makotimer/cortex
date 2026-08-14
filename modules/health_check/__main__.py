"""`python -m modules.health_check --json --date yesterday`.

Called by ops/scripts/nightly_fleet.sh from the host, which injects the result
into the fleet nightly email. cortex sends no nightly mail of its own.
"""
import sys

from .main import main_cli

sys.exit(main_cli())
