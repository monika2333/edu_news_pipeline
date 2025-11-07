from src.adapters import external_filter_model as model


def test_prompt_key_for_category_variants():
    assert model._prompt_key_for_category("internal_positive") == "internal"
    assert model._prompt_key_for_category("internal_negative") == "internal_negative"
    assert model._prompt_key_for_category("external_negative") == "external_negative"
    assert model._prompt_key_for_category("external_positive") == "external"
    assert model._prompt_key_for_category(None) == "external"
