from tests.test_looks import run_next_job, upload


def test_style_memory_learns_from_uploads_and_saves(client, runtime, user):
    before = client.get(f"/api/users/{user['id']}/style").json()
    assert before["signals"] == 0
    assert before["styles"][0]["id"] == "old_money"  # declared at sign-up

    upload(client, user["id"])
    run_next_job(runtime)
    product_id = next(iter(runtime.catalog.products))
    assert client.post(f"/api/users/{user['id']}/saved", json={"product_id": product_id}).status_code == 201

    after = client.get(f"/api/users/{user['id']}/style").json()
    assert after["signals"] > before["signals"]
    assert after["colours"], "colours observed in uploads are remembered"
    assert "你的穿搭偏向" in after["summary_zh"]


def test_saving_unknown_product_is_404(client, user):
    assert client.post(f"/api/users/{user['id']}/saved", json={"product_id": "nope"}).status_code == 404


def test_learned_styles_change_ranking_weights(client, runtime, user):
    from lookmate.db import SessionLocal
    from lookmate.models import User
    from lookmate.services.style_memory import user_context

    upload(client, user["id"])
    run_next_job(runtime)
    with SessionLocal() as s:
        weights = user_context(s, s.get(User, user["id"])).style_weights
    assert weights["old_money"] >= 0.3 and len(weights) > 1
