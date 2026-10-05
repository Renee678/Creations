from lookmate.api.looks import take_daily_quota
from lookmate.config import get_settings
from tests.test_looks import upload


def test_access_code_required_when_configured(client, user, monkeypatch):
    monkeypatch.setattr(get_settings(), "access_code", "lookmate-demo")
    assert upload(client, user["id"]).status_code == 401
    ok = client.post("/api/looks", data={"user_id": user["id"]}, headers={"X-Access-Code": "lookmate-demo"},
                     files={"image": ("a.png", b"\x89PNG\r\n\x1a\nguard", "image/png")})
    assert ok.status_code == 202
    assert client.get("/api/vocab").json()["access_code_required"] is True


def test_daily_quota_caps_new_analyses(client, user, monkeypatch):
    monkeypatch.setattr(get_settings(), "daily_look_limit", 1)
    assert upload(client, user["id"], data=b"\x89PNG\r\n\x1a\none").status_code == 202
    assert upload(client, user["id"], data=b"\x89PNG\r\n\x1a\ntwo").status_code == 429
    # Re-uploading an already analysed photo is free: it is deduplicated before the quota check.
    assert upload(client, user["id"], data=b"\x89PNG\r\n\x1a\none").status_code == 200


def test_zero_limit_means_unlimited(fake_redis):
    assert all(take_daily_quota(fake_redis, 0) for _ in range(5))
