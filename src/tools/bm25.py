"""Small dependency-free BM25 implementation for the frozen corpus."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

LATIN_OR_NUMBER = re.compile(r"[a-z0-9]+")
CJK_SEQUENCE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    normalized = text.lower()
    tokens = LATIN_OR_NUMBER.findall(normalized)
    for sequence in CJK_SEQUENCE.findall(normalized):
        tokens.extend(sequence)
        tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


@dataclass(frozen=True)
class SearchResult:
    source_id: str
    score: float


class BM25Index:
    def __init__(
        self,
        documents: dict[str, str],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.documents = documents
        self.term_frequencies = {
            source_id: Counter(tokenize(text))
            for source_id, text in documents.items()
        }
        self.lengths = {
            source_id: sum(frequencies.values())
            for source_id, frequencies in self.term_frequencies.items()
        }
        self.average_length = (
            sum(self.lengths.values()) / len(self.lengths) if self.lengths else 0.0
        )
        self.document_frequency: Counter[str] = Counter()
        for frequencies in self.term_frequencies.values():
            self.document_frequency.update(frequencies.keys())

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if max_results < 1:
            raise ValueError("max_results must be at least 1")
        terms = tokenize(query)
        if not terms or not self.documents:
            return []
        document_count = len(self.documents)
        scored: list[SearchResult] = []
        for source_id, frequencies in self.term_frequencies.items():
            score = 0.0
            document_length = self.lengths[source_id]
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                doc_frequency = self.document_frequency[term]
                inverse_document_frequency = math.log(
                    1 + (document_count - doc_frequency + 0.5) / (doc_frequency + 0.5)
                )
                normalization = frequency + self.k1 * (
                    1
                    - self.b
                    + self.b * document_length / max(self.average_length, 1)
                )
                score += inverse_document_frequency * (
                    frequency * (self.k1 + 1) / normalization
                )
            if score > 0:
                scored.append(SearchResult(source_id=source_id, score=score))
        scored.sort(key=lambda result: (-result.score, result.source_id))
        return scored[:max_results]
