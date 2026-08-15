"""Deterministic citation scoring for P6.4 RAG acceptance."""

from __future__ import annotations

from dataclasses import dataclass

from omnibase.agent_practice.contracts import CitationClaim, EvidenceChunk


@dataclass(frozen=True, slots=True)
class ExpectedFact:
    fact_id: str
    supporting_chunk_ids: frozenset[str]
    required_text: str | None = None

    def __post_init__(self) -> None:
        if not self.fact_id or not self.supporting_chunk_ids:
            raise ValueError("practice_expected_fact_invalid")
        if self.required_text is not None and not self.required_text.strip():
            raise ValueError("practice_expected_fact_text_invalid")


@dataclass(frozen=True, slots=True)
class CitationScore:
    expected_fact_count: int
    claimed_expected_fact_count: int
    supported_claim_count: int
    unsupported_claim_count: int
    missing_fact_count: int
    wrong_chunk_count: int
    unknown_chunk_count: int
    statement_mismatch_count: int
    fact_precision: float
    fact_recall: float
    citation_precision: float
    citation_recall: float
    passed: bool


def score_citations(
    *,
    claims: tuple[CitationClaim, ...],
    expected_facts: tuple[ExpectedFact, ...],
    evidence: tuple[EvidenceChunk, ...],
) -> CitationScore:
    """Score exact fact/chunk bindings without asking an LLM to grade itself."""

    expected = {
        item.fact_id: (item.supporting_chunk_ids, item.required_text) for item in expected_facts
    }
    if len(expected) != len(expected_facts):
        raise ValueError("practice_expected_fact_duplicate")
    known_chunks = {item.chunk_id for item in evidence}
    if len(known_chunks) != len(evidence):
        raise ValueError("practice_evidence_chunk_duplicate")
    claim_fact_ids = [claim.fact_id for claim in claims]
    if len(set(claim_fact_ids)) != len(claim_fact_ids):
        raise ValueError("practice_claim_fact_duplicate")
    claimed_expected: set[str] = set()
    supported = 0
    unsupported = 0
    correct_citations = 0
    total_citations = 0
    expected_citations_seen: set[tuple[str, str]] = set()
    wrong_chunks = 0
    unknown_chunks = 0
    statement_mismatches = 0
    for claim in claims:
        expected_item = expected.get(claim.fact_id)
        supports = expected_item[0] if expected_item is not None else None
        required_text = expected_item[1] if expected_item is not None else None
        statement_matches = (
            required_text is None or required_text.casefold() in claim.statement.casefold()
        )
        claim_supported = supports is not None and bool(claim.citation_chunk_ids)
        if expected_item is not None:
            claimed_expected.add(claim.fact_id)
            if not statement_matches:
                statement_mismatches += 1
                claim_supported = False
        for chunk_id in claim.citation_chunk_ids:
            total_citations += 1
            if chunk_id not in known_chunks:
                unknown_chunks += 1
                claim_supported = False
            elif supports is not None and chunk_id in supports:
                correct_citations += 1
                expected_citations_seen.add((claim.fact_id, chunk_id))
            else:
                wrong_chunks += 1
                claim_supported = False
        if claim_supported:
            supported += 1
        else:
            unsupported += 1
    expected_pairs = {
        (fact_id, chunk_id)
        for fact_id, (chunk_ids, _) in expected.items()
        for chunk_id in chunk_ids
    }
    expected_count = len(expected)
    fact_precision = supported / len(claims) if claims else 0.0
    fact_recall = len(claimed_expected) / expected_count if expected_count else 1.0
    citation_precision = correct_citations / total_citations if total_citations else 0.0
    citation_recall = len(expected_citations_seen) / len(expected_pairs) if expected_pairs else 1.0
    missing = expected_count - len(claimed_expected)
    passed = bool(
        claims
        and unsupported == 0
        and missing == 0
        and wrong_chunks == 0
        and unknown_chunks == 0
        and statement_mismatches == 0
        and fact_precision == 1.0
        and fact_recall == 1.0
        and citation_precision == 1.0
        and citation_recall == 1.0
    )
    return CitationScore(
        expected_fact_count=expected_count,
        claimed_expected_fact_count=len(claimed_expected),
        supported_claim_count=supported,
        unsupported_claim_count=unsupported,
        missing_fact_count=missing,
        wrong_chunk_count=wrong_chunks,
        unknown_chunk_count=unknown_chunks,
        statement_mismatch_count=statement_mismatches,
        fact_precision=fact_precision,
        fact_recall=fact_recall,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        passed=passed,
    )


__all__ = ["CitationScore", "ExpectedFact", "score_citations"]
