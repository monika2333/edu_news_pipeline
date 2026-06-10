from __future__ import annotations

from typing import Any

from src.adapters import title_cluster


def test_hf_hub_etag_timeout_default_is_20() -> None:
    assert title_cluster._DEFAULT_HF_HUB_ETAG_TIMEOUT == "20"


class _FakeSentenceTransformer:
    calls: list[tuple[str, bool]] = []
    fail_local = False

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        local_files_only = bool(kwargs.get("local_files_only", False))
        self.calls.append((model_name, local_files_only))
        if local_files_only and self.fail_local:
            raise OSError("missing local model")


def test_load_model_prefers_local_cache(monkeypatch) -> None:
    monkeypatch.setattr(title_cluster, "_model", None)
    monkeypatch.setattr(title_cluster, "SentenceTransformer", _FakeSentenceTransformer)
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_local = False

    model = title_cluster._get_model()

    assert isinstance(model, _FakeSentenceTransformer)
    assert _FakeSentenceTransformer.calls == [("BAAI/bge-large-zh", True)]


def test_load_model_falls_back_to_online_when_local_cache_missing(monkeypatch) -> None:
    monkeypatch.setattr(title_cluster, "_model", None)
    monkeypatch.setattr(title_cluster, "SentenceTransformer", _FakeSentenceTransformer)
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_local = True

    model = title_cluster._get_model()

    assert isinstance(model, _FakeSentenceTransformer)
    assert _FakeSentenceTransformer.calls == [
        ("BAAI/bge-large-zh", True),
        ("BAAI/bge-large-zh", False),
    ]
