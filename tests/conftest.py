import pytest


@pytest.fixture(autouse=True)
def isolated_feedback_database(tmp_path, monkeypatch):
    # Tests and launcher subprocesses must never consume or modify a user's reviews.
    monkeypatch.setenv('COLLAGEN_FEEDBACK_DIR', str(tmp_path / 'feedback'))
