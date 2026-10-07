"""Merchant inference without any model: gateway cleaning, a small alias table, a trailing
" - Merchant" in a description, and what the user's own entries already say. Pure functions.

Everything here is a *suggestion* or a conservative rewrite: a merchant the user typed is never
replaced by a guess, and an alias is only applied when the whole name is recognized.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from financas.domain.models import TransactionKind
from financas.domain.services.text import clean_merchant, normalize_search

# Payment gateways put their own name in front of the merchant ("PG *LOJA", "MP*MERCADOLIVRE").
_GATEWAY = re.compile(
    r"^\s*(?:pg|pag|mp|cielo|stone|sumup)\s*\*\s*",
    re.IGNORECASE,
)
_TRAILING_ID = re.compile(r"(?:\s+[#*]?\d[\d./-]*)+\s*$")  # "MERCADOLIVRE 123" -> "MERCADOLIVRE"

# (pattern on the normalized text with spaces and punctuation removed, canonical name)
_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), name)
    for pattern, name in (
        (r"^mercadolivre", "Mercado Livre"),
        (r"^mercadopago", "Mercado Pago"),
        (r"^shopee", "Shopee"),
        (r"^uber(?!eats)", "Uber"),
        (r"^ubereats", "Uber Eats"),
        (r"^ifood", "iFood"),
        (r"^amazon", "Amazon"),
        (r"^aliexpress", "AliExpress"),
        (r"^netflix", "Netflix"),
        (r"^spotify", "Spotify"),
        (r"^kabum", "Kabum"),
        (r"^magazineluiza|^magalu", "Magazine Luiza"),
        (r"^americanas", "Americanas"),
        (r"^rappi", "Rappi"),
        (r"^99(?:app|pop|food|taxi)?$", "99"),
    )
)

# " - Merchant" at the end of a description: the hyphen must have a space on both sides, so
# "Wi-Fi" and "Pré-pago" are left alone. The spec's `^(.*?)\s*-\s*([^-\n]+)$`, tightened.
_SUFFIX = re.compile(r"^(.*?)\s+-\s+([^-\n]+)$")
_MONTHS = frozenset(
    [
        "janeiro",
        "fevereiro",
        "marco",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
        "jan",
        "fev",
        "mar",
        "abr",
        "mai",
        "jun",
        "jul",
        "ago",
        "set",
        "out",
        "nov",
        "dez",
    ]
)
MIN_SUFFIX, MAX_SUFFIX = 2, 40


def strip_gateway(text: str) -> str:
    """``"PAG*MERCADOLIVRE 123"`` -> ``"MERCADOLIVRE 123"`` (one gateway prefix, if any)."""
    return _GATEWAY.sub("", text.strip(), count=1).strip()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_search(text))


def alias_of(text: str) -> str | None:
    """The canonical merchant when ``text`` mentions a known alias: gateway prefix and trailing
    ids are ignored and the alias may start at any word ("MERCADO LIVRE SP", "Estorno SHOPEE",
    "PAGAMENTO UBER *TRIP" -> "Mercado Livre", "Shopee", "Uber")."""
    cleaned = _TRAILING_ID.sub("", strip_gateway(text))
    words = [w for w in (_squash(word) for word in cleaned.split()) if w]
    for start in range(len(words)):
        squashed = "".join(words[start:])
        for pattern, name in _ALIASES:
            if pattern.search(squashed):
                return name
    return None


def canonical_merchant(raw: str) -> str | None:
    """A merchant name as it should be stored: an alias becomes its canonical spelling; any other
    name only loses the gateway prefix and extra spaces (the user's casing is kept)."""
    alias = alias_of(raw)
    if alias is not None:
        return alias
    return clean_merchant(strip_gateway(raw))


@dataclass(frozen=True)
class SuffixSplit:
    description: str  # without the " - Merchant" part
    merchant: str  # canonical


def split_suffix(description: str) -> SuffixSplit | None:
    """``"Mouse Gamer - Kabum"`` -> description ``"Mouse Gamer"``, merchant ``"Kabum"``.

    Only a clear suffix counts: 2 to 40 characters with a letter, not a month, not a number or
    an installment ("3/10"), and something must be left of it.
    """
    match = _SUFFIX.match(description.strip())
    if match is None:
        return None
    head, tail = match.group(1).strip(), match.group(2).strip()
    if not head or not MIN_SUFFIX <= len(tail) <= MAX_SUFFIX:
        return None
    if not re.search(r"[^\W\d_]", tail) or normalize_search(tail) in _MONTHS:
        return None
    if re.fullmatch(r"[\d\s./-]+", tail):
        return None
    merchant = canonical_merchant(tail)
    return SuffixSplit(head, merchant) if merchant else None


def settle_description(description: str, merchant: str | None) -> tuple[str, str | None]:
    """Manual entry: when no merchant was typed and the description ends in " - Merchant", the
    suffix becomes the (canonical) merchant and leaves the description. Otherwise unchanged."""
    if merchant:
        return description, merchant
    split = split_suffix(description)
    return (split.description, split.merchant) if split else (description, merchant)


# --- what the user's own entries say --------------------------------------------------------


@dataclass(frozen=True)
class MerchantLookup:
    """Learned from entries that already have a merchant: no model, just counting."""

    by_description: Mapping[str, str]  # normalized description -> its most frequent merchant
    known: Mapping[str, str]  # normalized merchant -> display name (most frequent spelling)


def build_lookup(pairs: Iterable[tuple[str, str]]) -> MerchantLookup:
    """``pairs`` are ``(description, merchant)`` of entries the user already filled in."""
    per_description: dict[str, Counter[str]] = defaultdict(Counter)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for description, merchant in pairs:
        name = clean_merchant(merchant)
        if not name:
            continue
        key = normalize_search(description)
        if key:
            per_description[key][name] += 1
        spellings[normalize_search(name)][name] += 1

    def top(counter: Counter[str]) -> str:
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

    return MerchantLookup(
        {key: top(counter) for key, counter in per_description.items()},
        {key: top(counter) for key, counter in spellings.items()},
    )


MIN_KNOWN = 4  # shorter names would match inside unrelated words


def infer_merchant(description: str, lookup: MerchantLookup | None = None) -> str | None:
    """A merchant for ``description``, or ``None``. In order: a trailing " - Merchant"; a known
    alias in the text; the merchant most often used with this exact description; the longest
    known merchant name that appears in the text."""
    if (split := split_suffix(description)) is not None:
        return split.merchant
    if (alias := alias_of(description)) is not None:
        return alias
    if lookup is None:
        return None
    key = normalize_search(strip_gateway(description))
    if key in lookup.by_description:
        return lookup.by_description[key]
    padded = f" {key} "
    best = max(
        (name for name in lookup.known if len(name) >= MIN_KNOWN and f" {name} " in padded),
        key=len,
        default=None,
    )
    return lookup.known[best] if best else None


# --- the backfill, as a pure plan ---------------------------------------------------------------


class ChangeReason(StrEnum):
    SUFFIX = "suffix"  # taken from " - Merchant" (the description is cleaned)
    ALIAS = "alias"  # a known alias in the description
    LEARNED = "learned"  # from what the user's own entries say
    CANONICALIZED = "canonicalized"  # an alias spelling replaced by the canonical name


@dataclass(frozen=True)
class BackfillRow:
    id: str
    kind: TransactionKind
    description: str
    merchant: str | None


@dataclass(frozen=True)
class BackfillChange:
    id: str
    merchant: str  # the merchant to store
    description: str | None  # the cleaned description, when a suffix was stripped
    reason: ChangeReason


_BACKFILL_KINDS = frozenset({TransactionKind.EXPENSE, TransactionKind.REFUND})


def plan_backfill(rows: Sequence[BackfillRow]) -> list[BackfillChange]:
    """What to write so every expense and refund carries a canonical merchant when one can be
    told. A merchant the user typed stays unless it is an alias spelling ("mercadolivre"). The
    lookup is built from the rows as they are *before* the plan: a guess never feeds another."""
    lookup = build_lookup((r.description, r.merchant) for r in rows if r.merchant)
    changes: list[BackfillChange] = []
    for row in rows:
        if row.kind not in _BACKFILL_KINDS:
            continue
        if row.merchant:
            alias = alias_of(row.merchant)
            if alias is not None and alias != row.merchant:
                changes.append(BackfillChange(row.id, alias, None, ChangeReason.CANONICALIZED))
            continue
        if (split := split_suffix(row.description)) is not None:
            changes.append(
                BackfillChange(row.id, split.merchant, split.description, ChangeReason.SUFFIX)
            )
        elif (alias := alias_of(row.description)) is not None:
            changes.append(BackfillChange(row.id, alias, None, ChangeReason.ALIAS))
        elif (learned := infer_merchant(row.description, lookup)) is not None:
            changes.append(BackfillChange(row.id, learned, None, ChangeReason.LEARNED))
    return changes
