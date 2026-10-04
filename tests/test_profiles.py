from lookmate.services.body import fit_adjustment

PROFILE = {
    "nickname": "Renee", "height_cm": 165, "weight_kg": 55, "age": 26,
    "body_shape": "pear", "preferred_styles": ["old_money", "minimalist"], "budget_per_item": 30,
}


def test_create_read_update_profile(client):
    created = client.post("/api/users", json=PROFILE)
    assert created.status_code == 201
    body = created.json()
    assert body["bmi"] == 20.2 and "A 字" in body["fit_advice"]

    uid = body["id"]
    assert client.get(f"/api/users/{uid}").json()["nickname"] == "Renee"

    updated = client.put(f"/api/users/{uid}", json={**PROFILE, "body_shape": "hourglass"}).json()
    assert updated["body_shape"] == "hourglass" and "腰线" in updated["fit_advice"]


def test_profile_validation(client):
    assert client.post("/api/users", json={**PROFILE, "height_cm": 40}).status_code == 422
    assert client.post("/api/users", json={**PROFILE, "body_shape": "banana"}).status_code == 422
    assert client.post("/api/users", json={**PROFILE, "preferred_styles": ["goth_core"]}).status_code == 422


def test_missing_profile_is_404(client):
    assert client.get("/api/users/99999").status_code == 404


def test_fit_adjustment_rewards_flattering_cuts_and_penalises_others():
    assert fit_adjustment("pear", "bottom", "High-waisted wide-leg trousers") > 0
    assert fit_adjustment("pear", "bottom", "Low-rise skinny jeans") < 0
    assert fit_adjustment("unsure", "bottom", "Low-rise skinny jeans") == 0
