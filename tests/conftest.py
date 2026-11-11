"""Keep all persistent test state inside pytest-managed temporary directories."""
import pytest

@pytest.fixture(autouse=True)
def isolated_workspace_directory(monkeypatch, tmp_path):
    monkeypatch.setenv('AEGIS_DATA_DIR', str(tmp_path / 'application-data'))
    monkeypatch.setenv('AEGIS_WEB_DATA_DIR', str(tmp_path / 'web-data'))
