from llm_sentinel.ratelimit.estimate import estimate_tokens


def test_estimate_tokens_scales_with_length() -> None:
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("a" * 100) == 25


def test_estimate_tokens_never_zero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a") == 1
