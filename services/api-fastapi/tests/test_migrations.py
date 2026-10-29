import os
import subprocess
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]


def test_models_match_migrations():
    """`alembic check` fails if the models have drifted from the migration history."""
    result = subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=SERVICE_DIR, env=os.environ.copy(),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
