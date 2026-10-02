"""Data of a parsed phrase: what was understood, from which words, and what is still ambiguous."""

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class FragmentKind(StrEnum):
    DESCRIPTION = "description"
    AMOUNT = "amount"  # the purchase price / total, or the single amount of an entry
    INSTALLMENT_AMOUNT = "installment_amount"  # "10x de 450": the value of each installment
    INSTALLMENTS = "installments"  # "10x", "10 vezes", "à vista"
    DATE = "date"
    ACCOUNT = "account"


class AmountRole(StrEnum):
    """What a lone amount next to an installment count means."""

    TOTAL = "total"
    EACH = "each"


class AmbiguityCode(StrEnum):
    AMOUNT_ROLE = "AMOUNT_ROLE"  # one amount and N installments: total or each?
    ACCOUNT = "ACCOUNT_AMBIGUOUS"  # the words fit more than one account


@dataclass(frozen=True)
class Fragment:
    """A stretch of the phrase that was understood: ``text[start:end]`` is what the user typed."""

    kind: FragmentKind
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Ambiguity:
    code: AmbiguityCode
    amount_cents: int | None = None
    installments: int | None = None
    candidates: tuple[str, ...] = ()  # account ids


@dataclass(frozen=True)
class AccountRef:
    """One account the phrase may name. ``aliases`` are extra words (the institution's name)."""

    id: str
    nickname: str
    is_card: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vocabulary:
    """The closed vocabulary the parser knows: the user's own account and card nicknames."""

    accounts: tuple[AccountRef, ...] = ()


@dataclass(frozen=True)
class PhraseDraft:
    """A draft, never a decision: the kind and the category are not here on purpose (9.1, 9.2)."""

    text: str
    description: str
    price_cents: int | None  # the cash price / total of the purchase (or a plain entry's amount)
    installment_cents: int | None  # the value of each installment, when the phrase gave it
    installments: int | None  # None: not said; 1: "à vista"
    on_date: dt.date | None  # None: no date in the phrase (the caller defaults to today)
    account_id: str | None
    account_candidates: tuple[str, ...] = ()
    fragments: tuple[Fragment, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    what_if: bool = False  # starts with "e se"
    cash: bool = False  # "à vista"
    amount_role: AmountRole | None = None  # how a lone amount next to N installments was read

    @property
    def amount_cents(self) -> int | None:
        """The one amount a plain entry needs: the price, else installments × installment value."""
        if self.price_cents is not None:
            return self.price_cents
        if self.installment_cents is not None:
            return self.installment_cents * (self.installments or 1)
        return None

    @property
    def is_installment_purchase(self) -> bool:
        return (self.installments or 1) > 1

    def has_ambiguity(self, code: AmbiguityCode) -> bool:
        return any(a.code is code for a in self.ambiguities)

    def understood(self, kind: FragmentKind) -> bool:
        return any(f.kind is kind for f in self.fragments)
