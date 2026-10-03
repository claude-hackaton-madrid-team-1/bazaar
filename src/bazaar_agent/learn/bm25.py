"""Okapi BM25 over learning texts: the lexical half of the hybrid recall.

Small on purpose (no dependency): the corpus is a few hundred to a few thousand short lessons, rebuilt
in about a millisecond. Card refs (`LAV-06`) stay one token and also count as their set (`lav`) and
number, so "LAV-06", "lav" and "06" all match; prices are tokens too ("22"), which is what lets a query
about a 22 P ask find the lesson about a 22 P fill.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

K1 = 1.5
B = 0.75
_WORD = re.compile(r"[a-z0-9]+(?:[-_:][a-z0-9]+)*")
STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it", "its"}
    | {"of", "on", "or", "our", "the", "this", "to", "was", "we", "with"}
)


def tokens(text: str) -> list[str]:
    """Lower-case words; a compound (`lav-06`, `card:uncommon`) also yields its parts."""
    out: list[str] = []
    for word in _WORD.findall(text.lower()):
        if word in STOPWORDS:
            continue
        out.append(word)
        parts = re.split(r"[-_:]", word)
        if len(parts) > 1:
            out.extend(p for p in parts if p and p not in STOPWORDS)
    return out


@dataclass(frozen=True)
class BM25:
    docs: tuple[tuple[str, ...], ...]
    df: dict[str, int]
    avgdl: float

    @classmethod
    def build(cls, texts: Sequence[str]) -> BM25:
        docs = tuple(tuple(tokens(t)) for t in texts)
        df: Counter[str] = Counter()
        for doc in docs:
            df.update(set(doc))
        avgdl = sum(len(d) for d in docs) / len(docs) if docs else 0.0
        return cls(docs, dict(df), avgdl)

    def idf(self, term: str) -> float:
        n, df = len(self.docs), self.df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> list[float]:
        terms = [t for t in dict.fromkeys(tokens(query)) if t in self.df]
        out = []
        for doc in self.docs:
            if not doc or not terms:
                out.append(0.0)
                continue
            counts, norm = Counter(doc), K1 * (1 - B + B * len(doc) / (self.avgdl or 1.0))
            out.append(sum(self.idf(t) * counts[t] * (K1 + 1) / (counts[t] + norm) for t in terms if counts[t]))
        return out

    def ranked(self, query: str, top: int) -> list[int]:
        """Indexes of the best `top` documents with a positive score, best first."""
        scored = [(s, i) for i, s in enumerate(self.scores(query)) if s > 0]
        return [i for _, i in sorted(scored, key=lambda si: (-si[0], si[1]))[:top]]
