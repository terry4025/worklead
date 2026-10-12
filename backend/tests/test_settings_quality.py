import pytest


@pytest.mark.parametrize("key,value", [
    ("daily_cost_cap", -1),
    ("monthly_cost_cap", -1),
    ("daily_cost_cap", True),
    ("monthly_cost_cap", 1.5),
    ("enabled", 100),
    ("external_transfer_consent", None),
])
def test_invalid_ai_settings_are_rejected_without_corrupting_saved_settings(client, key, value):
    before = client.get("/v1/settings").json()["ai"]
    response = client.patch("/v1/settings", json={"ai": {key: value}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get("/v1/settings").json()["ai"] == before


def test_zero_and_unset_cost_caps_roundtrip_as_valid_settings(client):
    response = client.patch("/v1/settings", json={"ai": {"daily_cost_cap": 0, "monthly_cost_cap": None}})
    assert response.status_code == 200
    assert response.json()["ai"]["daily_cost_cap"] == 0
    assert response.json()["ai"]["monthly_cost_cap"] is None
