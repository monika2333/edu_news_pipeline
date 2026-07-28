from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Sequence

from src.console.submission_archive_config import (
    LINK_BODY_CHARS,
    LINK_COARSE_TOP_K,
    LINK_TITLE_MIN,
    link_auto_threshold,
    link_review_threshold,
)
from src.console.submission_archive_parser import (
    normalize_submission_text,
    normalized_title_hash,
)


@dataclass(slots=True, frozen=True)
class LinkCandidate:
    article_id: str
    title: str
    body: str = ""


@dataclass(slots=True, frozen=True)
class LinkResult:
    status: str
    article_id: Optional[str]
    best_candidate_article_id: Optional[str]
    title_score: float
    body_score: float
    combined_score: float


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _candidate_scores(
    archive_title: str,
    archive_body: str,
    candidate: LinkCandidate,
) -> tuple[float, float, float]:
    normalized_archive_title = normalize_submission_text(archive_title)
    normalized_candidate_title = normalize_submission_text(candidate.title)
    title_score = SequenceMatcher(
        None,
        normalized_archive_title,
        normalized_candidate_title,
    ).ratio()
    normalized_archive_body = normalize_submission_text(archive_body)[
        :LINK_BODY_CHARS
    ]
    normalized_candidate_body = normalize_submission_text(candidate.body)[
        :LINK_BODY_CHARS
    ]
    body_score = (
        SequenceMatcher(
            None,
            normalized_archive_body,
            normalized_candidate_body,
        ).ratio()
        if normalized_archive_body and normalized_candidate_body
        else 0.0
    )
    combined = (
        0.6 * title_score + 0.4 * body_score
        if normalized_archive_body and normalized_candidate_body
        else title_score
    )
    return title_score, body_score, combined


def link_submission_item(
    archive_title: str,
    archive_body: str,
    candidates: Sequence[LinkCandidate],
    *,
    auto_threshold: Optional[float] = None,
    review_threshold: Optional[float] = None,
) -> LinkResult:
    if not candidates:
        return LinkResult(
            status="unmatched",
            article_id=None,
            best_candidate_article_id=None,
            title_score=0.0,
            body_score=0.0,
            combined_score=0.0,
        )

    archive_hash = normalized_title_hash(archive_title)
    for candidate in candidates:
        if archive_hash == normalized_title_hash(candidate.title):
            title_score, body_score, combined = _candidate_scores(
                archive_title,
                archive_body,
                candidate,
            )
            return LinkResult(
                status="exact",
                article_id=candidate.article_id,
                best_candidate_article_id=candidate.article_id,
                title_score=title_score,
                body_score=body_score,
                combined_score=combined,
            )

    archive_bigrams = _bigrams(normalize_submission_text(archive_title))
    coarse_candidates = sorted(
        candidates,
        key=lambda candidate: _jaccard(
            archive_bigrams,
            _bigrams(normalize_submission_text(candidate.title)),
        ),
        reverse=True,
    )[:LINK_COARSE_TOP_K]
    scored = [
        (*_candidate_scores(archive_title, archive_body, candidate), candidate)
        for candidate in coarse_candidates
    ]
    title_score, body_score, combined, best = max(
        scored,
        key=lambda entry: (entry[2], entry[0]),
    )
    auto = link_auto_threshold() if auto_threshold is None else auto_threshold
    review = (
        link_review_threshold()
        if review_threshold is None
        else review_threshold
    )
    if combined >= auto and title_score >= LINK_TITLE_MIN:
        status = "fuzzy"
        article_id: Optional[str] = best.article_id
    elif combined >= review:
        status = "pending"
        article_id = None
    else:
        status = "unmatched"
        article_id = None
    return LinkResult(
        status=status,
        article_id=article_id,
        best_candidate_article_id=best.article_id,
        title_score=title_score,
        body_score=body_score,
        combined_score=combined,
    )


__all__ = ["LinkCandidate", "LinkResult", "link_submission_item"]
