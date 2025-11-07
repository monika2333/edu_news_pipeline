from src.domain.external_filter import determine_candidate_category


def test_determine_candidate_category_variants():
    assert determine_candidate_category(True, "positive") == "internal_positive"
    assert determine_candidate_category(True, "negative") == "internal_negative"
    assert determine_candidate_category(False, "positive") == "external_positive"
    assert determine_candidate_category(False, "negative") == "external_negative"
    # sentiment fallback
    assert determine_candidate_category(True, None) == "internal"
    assert determine_candidate_category(False, "neutral") == "external"
