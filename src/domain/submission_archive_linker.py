from __future__ import annotations

import hashlib
import heapq
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Mapping, Optional, Sequence

from src.domain.submission_archive_config import (
    LINK_BODY_CHARS,
    LINK_COARSE_TOP_K,
    LINK_TITLE_MIN,
    link_auto_threshold,
    link_review_threshold,
)
from src.domain.submission_archive_parser import (
    normalize_submission_text,
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


@dataclass(slots=True, frozen=True)
class PreparedLinkCandidate:
    candidate: LinkCandidate
    normalized_title: str
    title_hash: str
    bigrams: frozenset[str]
    order_index: int


@dataclass(slots=True)
class LinkCandidateIndex:
    candidates: tuple[PreparedLinkCandidate, ...]
    first_by_hash: dict[str, PreparedLinkCandidate]


@dataclass(slots=True, frozen=True)
class LinkCandidateSelection:
    exact: Optional[PreparedLinkCandidate]
    coarse: tuple[PreparedLinkCandidate, ...]

    def required_article_ids(self) -> tuple[str, ...]:
        if self.exact is not None:
            return (self.exact.candidate.article_id,)
        return tuple(
            candidate.candidate.article_id
            for candidate in self.coarse
        )


def _normalized_title_hash(normalized_title: str) -> str:
    return hashlib.md5(normalized_title.encode("utf-8")).hexdigest()


def _bigrams(value: str) -> frozenset[str]:
    if len(value) < 2:
        return frozenset({value}) if value else frozenset()
    return frozenset(
        value[index : index + 2]
        for index in range(len(value) - 1)
    )


def _jaccard(
    left: frozenset[str],
    right: frozenset[str],
) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _candidate_scores(
    normalized_archive_title: str,
    normalized_archive_body: str,
    candidate: PreparedLinkCandidate,
    candidate_body: str,
) -> tuple[float, float, float]:
    title_score = SequenceMatcher(
        None,
        normalized_archive_title,
        candidate.normalized_title,
    ).ratio()
    normalized_candidate_body = normalize_submission_text(candidate_body)[
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


def build_link_candidate_index(
    candidates: Sequence[LinkCandidate],
) -> LinkCandidateIndex:
    prepared: list[PreparedLinkCandidate] = []
    first_by_hash: dict[str, PreparedLinkCandidate] = {}
    for order_index, candidate in enumerate(candidates):
        normalized_title = normalize_submission_text(candidate.title)
        item = PreparedLinkCandidate(
            candidate=candidate,
            normalized_title=normalized_title,
            title_hash=_normalized_title_hash(normalized_title),
            bigrams=_bigrams(normalized_title),
            order_index=order_index,
        )
        prepared.append(item)
        first_by_hash.setdefault(item.title_hash, item)
    return LinkCandidateIndex(
        candidates=tuple(prepared),
        first_by_hash=first_by_hash,
    )


def select_link_candidates(
    archive_title: str,
    candidate_index: LinkCandidateIndex,
) -> LinkCandidateSelection:
    normalized_archive_title = normalize_submission_text(archive_title)
    archive_hash = _normalized_title_hash(normalized_archive_title)
    exact = candidate_index.first_by_hash.get(archive_hash)
    if exact is not None:
        return LinkCandidateSelection(exact=exact, coarse=())

    archive_bigrams = _bigrams(normalized_archive_title)
    coarse_candidates = heapq.nlargest(
        LINK_COARSE_TOP_K,
        candidate_index.candidates,
        key=lambda candidate: (
            _jaccard(archive_bigrams, candidate.bigrams),
            -candidate.order_index,
        ),
    )
    return LinkCandidateSelection(
        exact=None,
        coarse=tuple(coarse_candidates),
    )


def score_link_candidate_selection(
    archive_title: str,
    archive_body: str,
    selection: LinkCandidateSelection,
    candidate_bodies: Mapping[str, str],
    *,
    auto_threshold: Optional[float] = None,
    review_threshold: Optional[float] = None,
) -> LinkResult:
    normalized_archive_title = normalize_submission_text(archive_title)
    normalized_archive_body = normalize_submission_text(archive_body)[
        :LINK_BODY_CHARS
    ]
    if selection.exact is not None:
        candidate = selection.exact
        title_score, body_score, combined = _candidate_scores(
            normalized_archive_title,
            normalized_archive_body,
            candidate,
            candidate_bodies.get(candidate.candidate.article_id, ""),
        )
        return LinkResult(
            status="exact",
            article_id=candidate.candidate.article_id,
            best_candidate_article_id=candidate.candidate.article_id,
            title_score=title_score,
            body_score=body_score,
            combined_score=combined,
        )
    if not selection.coarse:
        return LinkResult(
            status="unmatched",
            article_id=None,
            best_candidate_article_id=None,
            title_score=0.0,
            body_score=0.0,
            combined_score=0.0,
        )

    scored = [
        (
            *_candidate_scores(
                normalized_archive_title,
                normalized_archive_body,
                candidate,
                candidate_bodies.get(candidate.candidate.article_id, ""),
            ),
            candidate,
        )
        for candidate in selection.coarse
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
        article_id: Optional[str] = best.candidate.article_id
    elif combined >= review:
        status = "pending"
        article_id = None
    else:
        status = "unmatched"
        article_id = None
    return LinkResult(
        status=status,
        article_id=article_id,
        best_candidate_article_id=best.candidate.article_id,
        title_score=title_score,
        body_score=body_score,
        combined_score=combined,
    )


def link_submission_item(
    archive_title: str,
    archive_body: str,
    candidates: Sequence[LinkCandidate],
    *,
    auto_threshold: Optional[float] = None,
    review_threshold: Optional[float] = None,
) -> LinkResult:
    candidate_index = build_link_candidate_index(candidates)
    selection = select_link_candidates(archive_title, candidate_index)
    candidate_bodies = {
        candidate.article_id: candidate.body
        for candidate in candidates
    }
    return score_link_candidate_selection(
        archive_title,
        archive_body,
        selection,
        candidate_bodies,
        auto_threshold=auto_threshold,
        review_threshold=review_threshold,
    )


__all__ = [
    "LinkCandidate",
    "LinkCandidateIndex",
    "LinkCandidateSelection",
    "LinkResult",
    "build_link_candidate_index",
    "link_submission_item",
    "score_link_candidate_selection",
    "select_link_candidates",
]
