"""The regex parser: ``parse_phrase(text, vocabulary, today) -> PhraseDraft``.

Order of passes (each pass only takes words the previous ones left free): date, installments,
account, amounts, description. Understood stretches become :class:`Fragment` s so the UI can
underline them. The parser never invents a kind or a category, never raises on garbage, and
asks back (:class:`Ambiguity`) where two readings are possible: one amount next to N installments
(total or each?) and a nickname that fits two accounts.

Amounts accept ``1.234,56``, ``R$ 12,5``, ``4.200``, ``12.50`` (domain ``parse_brl`` rules); a
number followed by ``x``/``vezes``/``parcelas`` is an installment count; ``dia 5`` is the most
recent day 5; ``hoje``, ``ontem``, ``anteontem``, ``dd/mm``, ``dd/mm/aaaa`` and ``aaaa-mm-dd``
are dates.
"""

import calendar
import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass

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
from financas.domain.errors import DomainError
from financas.domain.money import parse_brl
from financas.domain.services.installments import MAX_INSTALLMENTS
from financas.domain.services.text import normalize_search

MAX_TEXT = 300  # longer phrases are cut: the field is a short sentence
MAX_CENTS = 100_000_000_00  # R$ 100 million: bigger is a typo, not a purchase

_STOP = frozenset(
    {"de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "num", "numa", "pelo"}
    | {"pela", "pelos", "pelas", "por", "para", "pra", "a", "o", "com"}
)
# never an alias on their own
_GENERIC_WORDS = frozenset(
    {"de", "da", "do", "das", "dos", "conta", "cartao", "corrente", "credito", "banco", "e"}
)
_WHAT_IF_LEAD = frozenset({"e", "se", "eu", "comprar", "comprasse"})
_INSTALLMENT_WORDS = frozenset({"vezes", "vez", "parcelas", "parcela", "vzs", "x"})
_UNIT_WORDS = frozenset({"reais", "real"})
_EACH_WORDS = frozenset({"cada"})
_LINK_BEFORE_EACH = frozenset({"de", "a", "com", "por"})
_TOTAL_WORDS = frozenset({"total"})
_WORD = re.compile(r"\S+")
_STRIP = ",;:!?()[]\"'"
_NUM = re.compile(r"\d[\d.]*(?:,\d+)?|\d+\.\d+")
_COUNT_X = re.compile(r"(\d{1,3})x")
_SLASH_DATE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    text: str  # as typed (accents kept), without edge punctuation
    norm: str  # casefolded, accent-free


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _WORD.finditer(text):
        raw = match.group()
        lead = len(raw) - len(raw.lstrip(_STRIP))
        core = raw.strip(_STRIP)
        while core.endswith("."):  # "4.200." -> "4.200"
            core = core[:-1]
        if not core:
            continue
        start = match.start() + lead
        tokens.append(_Token(start, start + len(core), core, normalize_search(core)))
    return tokens


def _amount_cents(token: str) -> int | None:
    """Cents for a token that looks like money, else ``None`` (never raises)."""
    body = token
    if body.casefold().startswith("r$"):
        body = body[2:]
    if not body or not body[0].isdigit() or not _NUM.fullmatch(body):
        return None
    try:
        cents = parse_brl(body)
    except DomainError:
        return None
    return cents if 0 < cents <= MAX_CENTS else None


def _date_of(token: _Token, today: dt.date) -> dt.date | None:
    if token.norm == "hoje":
        return today
    if token.norm == "ontem":
        return today - dt.timedelta(days=1)
    if token.norm == "anteontem":
        return today - dt.timedelta(days=2)
    try:
        if match := _SLASH_DATE.fullmatch(token.norm):
            day, month, year = match.groups()
            if year is None:
                year_number = today.year
            else:
                year_number = int(year) + (2000 if len(year) == 2 else 0)
            return dt.date(year_number, int(month), int(day))
        if match := _ISO_DATE.fullmatch(token.norm):
            return dt.date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
    return None


def _most_recent_day(day: int, today: dt.date) -> dt.date | None:
    """ "dia 5": day 5 of this month if it already came (or is today), else of an earlier month."""
    if not 1 <= day <= 31:
        return None
    for back in (0, 1, 2):
        index = today.year * 12 + today.month - 1 - back
        year, month = index // 12, index % 12 + 1
        if day <= calendar.monthrange(year, month)[1]:
            candidate = dt.date(year, month, day)
            if candidate <= today:
                return candidate
    return None


def _alias_map(accounts: Sequence[AccountRef]) -> dict[tuple[str, ...], dict[str, int]]:
    """alias (tuple of normalized words) -> {account id: priority}; 0 = the whole nickname."""
    aliases: dict[tuple[str, ...], dict[str, int]] = {}

    def add(words: tuple[str, ...], account: AccountRef, priority: int) -> None:
        if not words:
            return
        slot = aliases.setdefault(words, {})
        slot[account.id] = min(priority, slot.get(account.id, priority))

    for account in accounts:
        nickname_words = tuple(normalize_search(account.nickname).split())
        add(nickname_words, account, 0)
        for extra in account.aliases:
            add(tuple(normalize_search(extra).split()), account, 1)
        for word in nickname_words:
            if len(word) >= 2 and word not in _GENERIC_WORDS and not word.isdigit():
                add((word,), account, 1)
    return aliases


def _find_account(
    tokens: Sequence[_Token], used: list[bool], vocabulary: Vocabulary
) -> tuple[int, int, dict[str, int]] | None:
    """The longest alias match: ``(first token, last token, {account id: priority})``."""
    aliases = _alias_map(vocabulary.accounts)
    if not aliases:
        return None
    longest = max(len(words) for words in aliases)
    best: tuple[int, int, dict[str, int]] | None = None
    for i in range(len(tokens)):
        for length in range(min(longest, len(tokens) - i), 0, -1):
            span = range(i, i + length)
            if any(used[k] for k in span):
                continue
            key = tuple(tokens[k].norm for k in span)
            if key in aliases:
                if best is None or length > best[1] - best[0] + 1:
                    best = (i, i + length - 1, aliases[key])
                break
    return best


def parse_phrase(
    text: str,
    vocabulary: Vocabulary,
    today: dt.date,
    *,
    amount_role: AmountRole | None = None,
) -> PhraseDraft:
    """Read a phrase. ``amount_role`` is the user's answer to the "total or each?" question."""
    text = text[:MAX_TEXT]
    tokens = _tokens(text)
    used = [False] * len(tokens)
    fragments: list[Fragment] = []

    def take(kind: FragmentKind, first: int, last: int) -> None:
        for k in range(first, last + 1):
            used[k] = True
        start, end = tokens[first].start, tokens[last].end
        fragments.append(Fragment(kind, start, end, text[start:end]))

    # "e se ..." (a simulation): the lead words are consumed without being a fragment
    what_if = len(tokens) >= 2 and tokens[0].norm == "e" and tokens[1].norm == "se"
    if what_if:
        for k, token in enumerate(tokens):
            if token.norm not in _WHAT_IF_LEAD:
                break
            used[k] = True

    # 1. date
    on_date: dt.date | None = None
    for i, token in enumerate(tokens):
        if used[i]:
            continue
        if token.norm == "dia" and i + 1 < len(tokens) and not used[i + 1]:
            following = tokens[i + 1]
            if following.norm.isdigit():
                recent = (
                    _most_recent_day(int(following.norm), today)
                    if len(following.norm) <= 2
                    else None
                )
            else:
                recent = _date_of(following, today)
            if recent is not None:
                on_date = recent
                take(FragmentKind.DATE, i, i + 1)
                break
            continue
        as_date = _date_of(token, today)
        if as_date is not None:
            on_date = as_date
            take(FragmentKind.DATE, i, i)
            break

    # 2. installments: "10x", "10 x", "10 vezes", "à vista"
    installments: int | None = None
    cash = False
    installments_at: tuple[int, int] | None = None
    for i, token in enumerate(tokens):
        if used[i]:
            continue
        match = _COUNT_X.fullmatch(token.norm)
        if match and 1 <= int(match[1]) <= MAX_INSTALLMENTS:
            installments, installments_at = int(match[1]), (i, i)
        elif (
            token.norm.isdigit()
            and i + 1 < len(tokens)
            and not used[i + 1]
            and tokens[i + 1].norm in _INSTALLMENT_WORDS
            and 1 <= int(token.norm) <= MAX_INSTALLMENTS
            and len(token.norm) <= 3
        ):
            installments, installments_at = int(token.norm), (i, i + 1)
        elif token.norm in {"a", "avista"} and (
            token.norm == "avista" or (i + 1 < len(tokens) and tokens[i + 1].norm == "vista")
        ):
            installments, cash = 1, True
            installments_at = (i, i if token.norm == "avista" else i + 1)
        if installments_at is not None:
            take(FragmentKind.INSTALLMENTS, *installments_at)
            break

    # 3. account (nickname of an account or card)
    account_id: str | None = None
    candidates: tuple[str, ...] = ()
    ambiguities: list[Ambiguity] = []
    found = _find_account(tokens, used, vocabulary)
    if found is not None:
        first, last, scored = found
        take(FragmentKind.ACCOUNT, first, last)
        best = min(scored.values())
        ids = [a for a, p in scored.items() if p == best]
        by_id = {a.id: a for a in vocabulary.accounts}
        if len(ids) > 1 and (installments or 1) > 1:  # installments only exist on cards
            cards = [a for a in ids if by_id[a].is_card]
            ids = cards or ids
        candidates = tuple(sorted(ids))
        if len(candidates) == 1:
            account_id = candidates[0]
        else:
            ambiguities.append(Ambiguity(AmbiguityCode.ACCOUNT, candidates=candidates))

    # 4. amounts (the first two; further numbers stay in the description)
    price: int | None = None
    each: int | None = None
    role_read: AmountRole | None = None
    count = len(tokens)
    many = (installments or 1) > 1
    i = 0
    while i < count:
        if used[i]:
            i += 1
            continue
        first = last = i
        if tokens[i].norm == "r$" and i + 1 < count and not used[i + 1]:
            cents = _amount_cents(tokens[i + 1].text)
            last = i + 1
        else:
            cents = _amount_cents(tokens[i].text)
        if cents is None:
            i += 1
            continue
        role: AmountRole | None = None
        before = first - 1  # "10x de 450": right after the count (and "de"), it is each one's
        if before >= 0 and tokens[before].norm in _LINK_BEFORE_EACH:
            before -= 1
        if installments_at is not None and before == installments_at[1]:
            role = AmountRole.EACH
        after = last + 1
        if after < count and not used[after] and tokens[after].norm in _UNIT_WORDS:
            last, after = after, after + 1
        if role is None and after < count and not used[after]:
            word = tokens[after].norm
            if word in _EACH_WORDS:
                role, last = AmountRole.EACH, after
            elif word in _TOTAL_WORDS:
                role, last = AmountRole.TOTAL, after
            elif word == "por" and after + 1 < count and tokens[after + 1].norm == "parcela":
                role, last = AmountRole.EACH, after + 1
        if many and role is AmountRole.EACH and each is None:
            each = cents
            take(FragmentKind.INSTALLMENT_AMOUNT, first, last)
        elif price is None:
            price = cents
            take(FragmentKind.AMOUNT, first, last)
            if role is AmountRole.TOTAL:
                role_read = AmountRole.TOTAL
        elif many and each is None:
            each = cents  # a second amount next to the count: the installment value
            take(FragmentKind.INSTALLMENT_AMOUNT, first, last)
        else:
            break  # a spare number: it stays in the description
        i = last + 1

    # one amount next to N installments: total or each?
    if price is not None and each is None and (installments or 1) > 1:
        explicit = role_read or amount_role
        if explicit is AmountRole.EACH:
            price, each, role_read = None, price, AmountRole.EACH
            fragments[:] = [
                Fragment(FragmentKind.INSTALLMENT_AMOUNT, f.start, f.end, f.text)
                if f.kind is FragmentKind.AMOUNT
                else f
                for f in fragments
            ]
        else:
            role_read = AmountRole.TOTAL
            if explicit is None:
                ambiguities.append(Ambiguity(AmbiguityCode.AMOUNT_ROLE, price, installments))

    # 5. description: the free words, minus linking words next to understood ones
    kept: list[int] = []
    for k, token in enumerate(tokens):
        if used[k] or token.norm == "r$":
            continue
        if token.norm in _STOP:
            before_used = k == 0 or used[k - 1]
            after_used = k == len(tokens) - 1 or used[k + 1]
            if before_used or after_used:
                continue
        kept.append(k)
    # edges: a stop word left at the start or end of the description after the cleaning above
    while kept and tokens[kept[0]].norm in _STOP:
        kept.pop(0)
    while kept and tokens[kept[-1]].norm in _STOP:
        kept.pop()
    description = " ".join(tokens[k].text for k in kept)
    if description:
        description = description[0].upper() + description[1:]
    run_start = 0
    for index, k in enumerate(kept):
        if index == len(kept) - 1 or kept[index + 1] != k + 1:
            first_k = kept[run_start]
            fragments.append(
                Fragment(
                    FragmentKind.DESCRIPTION,
                    tokens[first_k].start,
                    tokens[k].end,
                    text[tokens[first_k].start : tokens[k].end],
                )
            )
            run_start = index + 1

    return PhraseDraft(
        text=text,
        description=description,
        price_cents=price,
        installment_cents=each,
        installments=installments,
        on_date=on_date,
        account_id=account_id,
        account_candidates=candidates,
        fragments=tuple(sorted(fragments, key=lambda f: f.start)),
        ambiguities=tuple(ambiguities),
        what_if=what_if,
        cash=cash,
        amount_role=role_read,
    )
