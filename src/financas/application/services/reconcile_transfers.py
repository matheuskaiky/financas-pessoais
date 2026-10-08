"""Pairing historical entries that are the two halves of one internal transfer (pure, no I/O).

The scanner never decides alone: it proposes pairs with a confidence, the user reads the dry run and
applies explicitly (CLAUDE.md rule 8, 9.1: the kind is never inferred automatically). Everything
here is a pure function over ``Candidate`` rows; eligibility (account kinds, plans, statements,
itemized entries) is decided by the use case that builds them.

Scoring (0-100): amounts equal and signs opposite on two different accounts is the entry ticket
(50); the same day adds 30, one day apart 15 (TED/DOC settle on the next business day); a transfer
keyword (PIX, TED, DOC, transf...) in either description adds 15; identical descriptions add 5.
A pair is *clear* when each side's best candidate is the other and no other candidate ties with
it; anything else is *ambiguous* and is reported but never paired.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

MIN_APPLY_CONFIDENCE = 70  # below this a pair is shown for review but not applied
MAX_DAYS_APART = 1
KEYWORDS = ("pix", "ted", "doc", "transf")  # matched on the normalized (accent-free) description


@dataclass(frozen=True)
class Candidate:
    """An entry that could be one half of a transfer."""

    id: str
    account_id: str
    posted_on: dt.date
    amount_cents: int  # signed
    description_search: str  # normalized: lower case, no accents
    already_transfer: bool = False  # a lone transfer leg (kind transfer, no pair yet)


@dataclass(frozen=True)
class PairProposal:
    outgoing: Candidate
    incoming: Candidate
    confidence: int

    @property
    def applicable(self) -> bool:
        return self.confidence >= MIN_APPLY_CONFIDENCE


@dataclass(frozen=True)
class Reconciliation:
    pairs: tuple[PairProposal, ...]  # clear pairs, newest first
    ambiguous: tuple[str, ...]  # ids that have a candidate but tie or are not mutual: never paired

    @property
    def applicable(self) -> tuple[PairProposal, ...]:
        return tuple(p for p in self.pairs if p.applicable)


def _has_keyword(text: str) -> bool:
    return any(word in text for word in KEYWORDS)


def score(outgoing: Candidate, incoming: Candidate) -> int | None:
    """Confidence of two entries being one transfer, or ``None`` when they cannot be."""
    if outgoing.account_id == incoming.account_id:
        return None
    if outgoing.amount_cents >= 0 or incoming.amount_cents <= 0:
        return None
    if -outgoing.amount_cents != incoming.amount_cents:
        return None
    days = abs((outgoing.posted_on - incoming.posted_on).days)
    if days > MAX_DAYS_APART:
        return None
    value = 50 + (30 if days == 0 else 15)
    if _has_keyword(outgoing.description_search) or _has_keyword(incoming.description_search):
        value += 15
    if outgoing.description_search and outgoing.description_search == incoming.description_search:
        value += 5
    return min(value, 100)


def find_pairs(candidates: list[Candidate]) -> Reconciliation:
    """Clear pairs among ``candidates`` and the groups that are too ambiguous to pair."""
    by_amount: dict[int, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_amount[abs(c.amount_cents)].append(c)
    edges: dict[str, list[tuple[int, str]]] = defaultdict(list)  # id -> [(score, other id)]
    known = {c.id: c for c in candidates}
    for group in by_amount.values():
        outs = [c for c in group if c.amount_cents < 0]
        ins = [c for c in group if c.amount_cents > 0]
        for out in outs:
            for inc in ins:
                value = score(out, inc)
                if value is not None:
                    edges[out.id].append((value, inc.id))
                    edges[inc.id].append((value, out.id))

    def best(entry_id: str) -> tuple[int, str] | None:
        """The top edge of an entry, or ``None`` when it has none or the top is tied."""
        ranked = sorted(edges.get(entry_id, []), reverse=True)
        if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
            return None
        return ranked[0]

    pairs: list[PairProposal] = []
    seen: set[str] = set()
    for entry_id in sorted(edges):
        top = best(entry_id)
        if top is None or entry_id in seen:
            continue
        other = best(top[1])
        if other is None or other[1] != entry_id:
            continue
        a, b = known[entry_id], known[top[1]]
        outgoing, incoming = (a, b) if a.amount_cents < 0 else (b, a)
        pairs.append(PairProposal(outgoing, incoming, top[0]))
        seen.update((entry_id, top[1]))
    leftovers = sorted(i for i in edges if i not in seen)
    pairs.sort(key=lambda p: (p.outgoing.posted_on, p.outgoing.id), reverse=True)
    return Reconciliation(tuple(pairs), tuple(leftovers))
