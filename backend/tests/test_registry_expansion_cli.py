import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "expand_registry_candidate.py"


def test_help_exits_without_running_ingestion():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert "NAFDAC Greenbook" in result.stdout
    assert "product pages" in result.stdout
    assert "Existing records:" not in result.stdout
