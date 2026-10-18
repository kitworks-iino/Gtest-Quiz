"""Model discovery must not mistake a free offer or another tier for free I/O."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from gtest_quiz.model_selection import ModelSelectionError, free_standard_models, resolve_model


def table(input_price="Free of charge", output_price="Free of charge", tier="Standard", *, extra=""):
    return (
        f"<section><h3>{tier}</h3><table><tr><th></th><th>Free Tier</th><th>Paid Tier</th></tr>"
        f"<tr><td>Input price</td><td>{input_price}</td><td>$1</td></tr>"
        f"<tr><td>Output price (including thinking tokens)</td><td>{output_price}</td><td>$5</td></tr>"
        f"{extra}</table></section>"
    )


def section(model_id, prices=None):
    return (
        f'<div class="models-section"><div class="heading-group"><h2 id="{model_id}">Model</h2>'
        f"<em><a><code>{model_id}</code></a></em></div></div>{prices if prices is not None else table()}"
    )


def api_model(name, actions=("generateContent",)):
    return SimpleNamespace(name=f"models/{name}", supported_actions=list(actions))


def select(ids, html, requested="auto", **kwargs):
    return resolve_model(
        None, requested, fetch_pricing=lambda: html,
        list_models=lambda: (api_model(name) for name in ids), **kwargs,
    )


def test_new_generation_is_discovered_without_a_model_id_change():
    ids = ["gemini-3.8-flash", "gemini-4-argon", "gemini-8.12-pro", "gemini-8.9-pro"]
    html = "".join(section(name) for name in ids)
    result = select(ids, html, checked_at=datetime(2026, 10, 18, tzinfo=timezone.utc))
    assert result.model_id == "gemini-8.12-pro"
    assert result.eligible_models == ("gemini-8.12-pro", "gemini-8.9-pro", "gemini-4-argon", "gemini-3.8-flash")
    assert result.checked_at == "2026-10-18T00:00:00+00:00"
    assert result.pricing_url.startswith("https://ai.google.dev/")
    assert "not the project's billing tier" in result.reason


def test_paid_flagship_does_not_override_free_model():
    html = section("gemini-4-argon", table("Not available", "Not available")) + section("gemini-3.8-flash")
    result = select(["gemini-4-argon", "gemini-3.8-flash"], html)
    assert result.model_id == "gemini-3.8-flash"
    assert result.eligible_models == ("gemini-3.8-flash",)
    with pytest.raises(ModelSelectionError, match="lacks verified free"):
        select(["gemini-4-argon", "gemini-3.8-flash"], html, "gemini-4-argon")


def test_same_version_prefers_family_quality_then_revision():
    ids = ["gemini-8-flash-lite", "gemini-8-pro", "gemini-8-flash", "gemini-8-argon", "gemini-8-argon-002"]
    result = select(ids, "".join(section(name) for name in ids))
    assert result.eligible_models == ("gemini-8-argon-002", "gemini-8-argon", "gemini-8-pro", "gemini-8-flash", "gemini-8-flash-lite")


@pytest.mark.parametrize("name", [
    "gemini-9-pro-preview", "gemini-9-pro-exp", "gemini-pro-latest", "gemini-9-flash-tts",
    "gemini-9-flash-image", "gemini-9-pro-cyber", "gemini-9-live", "gemini-robotics-er-9",
    "gemini-embedding-9", "gemini-9-ultra", "gemini-9-flash-native-audio",
])
def test_preview_specialized_aliases_and_unknown_families_are_excluded(name):
    result = select([name, "gemini-3.8-flash"], section(name) + section("gemini-3.8-flash"))
    assert result.eligible_models == ("gemini-3.8-flash",)


def test_standard_pricing_cannot_be_borrowed_from_batch_priority_or_next_model():
    html = section("gemini-8-pro", table("Not available", "Not available") + table(tier="Batch") + table(tier="Priority"))
    html += section("gemini-7-pro", table(tier="Batch"))
    html += section("gemini-6-pro")
    html += section("gemini-5-pro", table("Not available", "Not available"))
    assert free_standard_models(html) == {"gemini-6-pro"}


@pytest.mark.parametrize("input_price, output_price", [
    ("Free of charge", "Not available"), ("$0.00", "Free of charge"),
    ("Free of charge for the first 100 requests", "Free of charge"),
    ("Free of charge<sup>*</sup>", "Free of charge"), ("", "Free of charge"),
])
def test_both_prices_must_be_explicitly_free(input_price, output_price):
    assert not free_standard_models(section("gemini-8-pro", table(input_price, output_price)))


def test_conflicting_duplicate_pricing_fails_closed_for_that_model():
    html = section("gemini-8-pro") + section("gemini-8-pro", table("Not available", "Not available"))
    html += section("gemini-7-pro")
    assert free_standard_models(html) == {"gemini-7-pro"}


def test_duplicate_input_rows_and_missing_output_are_rejected():
    duplicate = "<tr><td>Input price</td><td>Free of charge</td><td>$1</td></tr>"
    assert not free_standard_models(section("gemini-8-pro", table(extra=duplicate)))
    missing = table().replace("Output price (including thinking tokens)", "Context caching price")
    assert not free_standard_models(section("gemini-8-pro", missing))


def test_free_column_is_identified_by_header_not_position():
    html = section("gemini-8-pro", "<h3>Standard</h3><table><tr><th></th><th>Paid Tier</th><th>Free Tier</th></tr>"
                   "<tr><td>Input price</td><td>$1</td><td>Free of charge</td></tr>"
                   "<tr><td>Output price</td><td>$5</td><td>Free of charge</td></tr></table>")
    assert free_standard_models(html) == {"gemini-8-pro"}


def test_only_models_available_for_generation_are_selected_and_sdk_pager_is_consumed():
    visited = []

    def models():
        for name, actions in [("gemini-9-pro", ["countTokens"]), ("gemini-8-pro", ["generateContent"])]:
            visited.append(name)
            yield {"name": f"models/{name}", "supported_actions": actions}

    client = SimpleNamespace(models=SimpleNamespace(list=models))
    result = resolve_model(client, fetch_pricing=lambda: section("gemini-9-pro") + section("gemini-8-pro") + section("gemini-7-pro"))
    assert result.model_id == "gemini-8-pro"
    assert visited == ["gemini-9-pro", "gemini-8-pro"]


def test_explicit_model_override_is_also_verified():
    html = section("gemini-8-pro") + section("gemini-7-pro")
    result = select(["gemini-8-pro", "gemini-7-pro"], html, "models/gemini-7-pro")
    assert result.model_id == "gemini-7-pro"
    assert result.reason.startswith("Explicit model validated")


@pytest.mark.parametrize("html", ["", "<html>Login required</html>", "# Gemini pricing", "<h2>gemini-8-pro</h2>Free of charge"])
def test_empty_unrecognized_or_changed_pricing_never_falls_back(html):
    with pytest.raises(ModelSelectionError):
        select(["gemini-8-pro"], html)


def test_no_overlap_does_not_fall_back_to_an_available_paid_model():
    with pytest.raises(ModelSelectionError, match="No available stable"):
        select(["gemini-8-pro"], section("gemini-7-pro"))


def test_network_failure_does_not_list_or_generate():
    def fail():
        raise OSError("unavailable")

    with pytest.raises(ModelSelectionError, match="could not be retrieved"):
        resolve_model(None, fetch_pricing=fail)


def test_api_pagination_failure_does_not_accept_partial_results():
    def partial():
        yield api_model("gemini-8-pro")
        raise OSError("second page unavailable")

    with pytest.raises(ModelSelectionError, match="could not be listed"):
        resolve_model(None, fetch_pricing=lambda: section("gemini-8-pro"), list_models=partial)


def test_timestamp_must_include_timezone():
    with pytest.raises(ModelSelectionError, match="timezone"):
        select(["gemini-8-pro"], section("gemini-8-pro"), checked_at=datetime(2026, 10, 18))
