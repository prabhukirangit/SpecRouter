"""Dynamic regex tokenization engine (usecase.md section 3.2).

Four-step normalization matrix, applied in order:
  1. CamelCase splitting        -> ``fetchSystemAlerts`` -> fetch system alerts
  2. Snake/format/URI cleansing -> ``_ - . / { }`` become whitespace
  3. Alphanumeric + lowercasing -> drop remaining punctuation, lowercase
  4. API stop-word filtering    -> drop protocol/version noise + English fillers

No external tokenizer servers; pure stdlib ``re``.
"""

from __future__ import annotations

import re

# Step 1: insert a boundary between a lowercase/digit and an uppercase letter,
# and between an acronym run and a following CamelCase word (e.g. "APIKey" -> "API Key").
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Step 2: structural separators that should become whitespace.
_SEPARATORS = re.compile(r"[_\-.\/{}\[\]()]+")

# Step 3: anything that is not an alphanumeric becomes a split point.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Step 4: API/protocol noise plus a small set of conversational fillers.
_API_STOPWORDS = frozenset(
    {
        "http", "https", "api", "rest", "json", "xml", "www",
        "v1", "v2", "v3", "v4", "url", "uri", "endpoint",
    }
)
_ENGLISH_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "to", "and", "or", "in", "on", "at",
        "by", "with", "from", "into", "get", "please", "me", "my", "is", "are",
        "this", "that", "all", "any", "some",
    }
)

# ``get`` is deliberately *not* dropped as an HTTP-verb stopword: it carries
# intent in queries like "get user billing". HTTP method routing is handled
# structurally elsewhere, so we keep it as a normal token.
_STOPWORDS = _API_STOPWORDS | (_ENGLISH_STOPWORDS - {"get"})


def tokenize(text: str | None) -> list[str]:
    """Normalize ``text`` into a list of lowercase content tokens."""
    if not text:
        return []
    spaced = _CAMEL_BOUNDARY.sub(" ", text)
    spaced = _SEPARATORS.sub(" ", spaced)
    spaced = spaced.lower()
    spaced = _NON_ALNUM.sub(" ", spaced)
    tokens = [t for t in spaced.split() if t and t not in _STOPWORDS]
    return tokens
