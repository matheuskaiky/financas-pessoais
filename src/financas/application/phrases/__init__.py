"""Plain-text phrases ("café 12,50 hoje nubank", "geladeira 4200 em 10x no bb") read by regex.

Deterministic, offline, pure. The same contract will be filled by an external language model when
the assistant exists (CLAUDE.md section 16): a draft with the fragments that were understood, never
a kind or a category (those stay the user's choice or a suggestion from existing data).
"""

from financas.application.phrases.model import (
    AccountRef,
    Ambiguity,
    AmbiguityCode,
    AmountRole,
    Fragment,
    FragmentKind,
    PhraseDraft,
    Vocabulary,
)
from financas.application.phrases.parser import parse_phrase

__all__ = [
    "AccountRef",
    "Ambiguity",
    "AmbiguityCode",
    "AmountRole",
    "Fragment",
    "FragmentKind",
    "PhraseDraft",
    "Vocabulary",
    "parse_phrase",
]
