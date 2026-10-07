"""The offline tests must behave the same on a machine whose .env sets an access code or keys."""
from lookmate.config import Settings


def test_tests_ignore_the_local_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ACCESS_CODE=from-dotenv\nFASHN_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.delenv("FASHN_API_KEY", raising=False)
    settings = Settings()
    assert settings.access_code == ""
    assert settings.fashn_api_key == ""
