from lookmate.services.body import fit_adjustment

PROFILE = {
    "nickname": "Renee", "height_cm": 165, "weight_kg": 55, "age": 26,
    "body_shape": "pear", "preferred_styles": ["old_money", "minimalist"], "budget_per_item": 30,
}


def test_create_read_update_profile(client):
    created = client.post("/api/users", json=PROFILE)
    assert created.status_code == 201
    body = created.json()
    assert body["bmi"] == 20.2 and "A-line" in body["fit_advice"]

    uid = body["id"]
    assert client.get(f"/api/users/{uid}").json()["nickname"] == "Renee"

    updated = client.put(f"/api/users/{uid}", json={**PROFILE, "body_shape": "hourglass"}).json()
    assert updated["body_shape"] == "hourglass" and "waist" in updated["fit_advice"]


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


def test_the_default_budget_per_item_is_50_and_a_saved_one_is_kept(client):
    """Renee (2026-10-06): $30 left almost nothing once the price range became a hard filter."""
    from lookmate.services.ranking import UserContext

    assert UserContext().budget_per_item == 50
    no_budget = {k: v for k, v in PROFILE.items() if k != "budget_per_item"}
    uid = client.post("/api/users", json=no_budget).json()["id"]
    assert client.get(f"/api/users/{uid}").json()["budget_per_item"] == 50
    lb = client.get(f"/api/users/{uid}/lookbook").json()
    assert lb["price_range"] == {"low": 0, "high": 75}, "up to 1.5x the $50 default"
    saved = client.post("/api/users", json=PROFILE).json()
    assert saved["budget_per_item"] == 30, "a budget the user chose stays theirs"
