from pathlib import Path
import subprocess
import sys

import desktop


def test_desktop_analysis_and_server_smoke():
    # Actual subprocess lifecycle and full analysis/export/workbook dependencies.
    result = subprocess.run(
        [sys.executable, str(Path(desktop.__file__)), '--smoke-test'],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Desktop smoke test passed' in result.stdout


def test_stop_does_not_terminate_an_unrelated_process():
    server = desktop.LocalServer()
    server.stop()  # Closing a launcher before startup is safe.
    assert server.process is None
