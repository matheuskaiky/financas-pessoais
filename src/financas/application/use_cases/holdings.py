"""Fixed-income holdings (Brazil): contract data, valuations, flows, redemption (9.6, 3b).

Contract data is display only. Taxes are never computed; the institution's net value is the
source. A holdings-level account has valuations per holding, never for the whole account.
"""

import datetime as dt
from dataclasses import dataclass, replace

from financas.application.use_cases._common import found, new_id
from financas.application.use_cases.transactions import (
    RegisterTransferCommand,
    build_transfer_legs,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    BalanceAnchor,
    HoldingStatus,
    Indexer,
    InstrumentType,
    InvestmentHolding,
    InvestmentTracking,
    Liquidity,
    RateMode,
    Transaction,
)
from financas.domain.ports import UnitOfWork, Work
from financas.domain.rules import validate_gross_balance, validate_holding
from financas.domain.services.holdings import suggest_fgc_covered
from financas.domain.services.text import clean_text


def _require_checking(uow: Work, account_id: str | None) -> None:
    """The other side of a contribution or redemption is a tracked checking account."""
    if account_id is None:
        return
    other = found(uow.accounts.get(account_id), "account")
    if other.kind is not AccountKind.CHECKING:
        raise DomainError("ACCOUNT_KIND_NOT_ALLOWED", account_kind=other.kind.value)


def _holdings_account(uow: Work, account_id: str) -> Account:
    account = found(uow.accounts.get(account_id), "account")
    if account.kind is not AccountKind.INVESTMENT:
        raise DomainError("INVESTMENT_REQUIRED")
    if account.tracking is not InvestmentTracking.HOLDINGS:
        raise DomainError("ACCOUNT_NOT_HOLDINGS_LEVEL")
    return account


@dataclass(frozen=True)
class RegisterHoldingCommand:
    account_id: str
    name: str
    instrument_type: InstrumentType
    issuer_id: str
    applied_on: dt.date
    principal_cents: int
    liquidity: Liquidity
    indexer: Indexer | None = None
    rate_mode: RateMode | None = None
    rate_bps: int | None = None
    maturity_on: dt.date | None = None
    liquid_from: dt.date | None = None
    fgc_covered: bool | None = None  # None: the suggestion for the instrument type
    is_emergency_fund: bool = False
    asset_class: AssetClass | None = None  # None: the account's class
    contribute: bool = False  # also register the principal as a contribution on ``applied_on``
    from_account_id: str | None = None  # checking account that paid it (None: not tracked)


class RegisterHolding:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RegisterHoldingCommand) -> InvestmentHolding:
        with self._uow as uow:
            account = _holdings_account(uow, cmd.account_id)
            found(uow.institutions.get(cmd.issuer_id), "institution")
            if cmd.contribute:
                _require_checking(uow, cmd.from_account_id)
            holding = InvestmentHolding(
                id=new_id(),
                account_id=account.id,
                name=clean_text(cmd.name),
                instrument_type=cmd.instrument_type,
                issuer_id=cmd.issuer_id,
                indexer=cmd.indexer,
                rate_mode=cmd.rate_mode,
                rate_bps=cmd.rate_bps,
                applied_on=cmd.applied_on,
                principal_cents=cmd.principal_cents,
                maturity_on=cmd.maturity_on,
                liquidity=cmd.liquidity,
                liquid_from=cmd.liquid_from,
                fgc_covered=(
                    suggest_fgc_covered(cmd.instrument_type)
                    if cmd.fgc_covered is None
                    else cmd.fgc_covered
                ),
                is_emergency_fund=cmd.is_emergency_fund,
                asset_class=cmd.asset_class or account.asset_class or AssetClass.FIXED_INCOME,
            )
            validate_holding(holding)
            uow.holdings.add(holding)
            if cmd.contribute:
                legs = build_transfer_legs(
                    uow,
                    RegisterTransferCommand(
                        from_account_id=cmd.from_account_id,
                        to_account_id=account.id,
                        posted_on=cmd.applied_on,
                        amount_cents=cmd.principal_cents,
                        holding_id=holding.id,
                    ),
                )
                uow.transactions.add_many(legs)
            uow.commit()
        return holding


class SetHoldingFlags:
    """``fgc_covered`` is suggested by type and editable; so is the emergency-fund mark."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self, holding_id: str, fgc_covered: bool, is_emergency_fund: bool
    ) -> InvestmentHolding:
        with self._uow as uow:
            holding = replace(
                found(uow.holdings.get(holding_id), "holding"),
                fgc_covered=fgc_covered,
                is_emergency_fund=is_emergency_fund,
            )
            uow.holdings.update(holding)
            uow.commit()
        return holding


@dataclass(frozen=True)
class RecordHoldingValuationCommand:
    holding_id: str
    on_date: dt.date
    balance_cents: int  # net redemption value, as the institution shows it
    gross_balance_cents: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class RecordHoldingValuationResult:
    anchor: BalanceAnchor
    computed_cents: int | None
    difference_cents: int | None  # informed - computed: the yield since the last valuation


class RecordHoldingValuation:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RecordHoldingValuationCommand) -> RecordHoldingValuationResult:
        from financas.domain.services.balances import AnchorPoint
        from financas.domain.services.investments import current_value

        validate_gross_balance(cmd.balance_cents, cmd.gross_balance_cents)
        with self._uow as uow:
            holding = found(uow.holdings.get(cmd.holding_id), "holding")
            _holdings_account(uow, holding.account_id)
            if holding.status is HoldingStatus.REDEEMED:
                raise DomainError("HOLDING_REDEEMED")
            others = [
                AnchorPoint(a.on_date, a.balance_cents)
                for a in uow.anchors.list_for_holding(holding.id)
                if a.on_date != cmd.on_date
            ]
            before = [a for a in others if a.on_date < cmd.on_date]
            computed = current_value(
                before, uow.transactions.movements_for_holding(holding.id), cmd.on_date
            )
            anchor = BalanceAnchor(
                id=new_id(),
                account_id=holding.account_id,
                on_date=cmd.on_date,
                balance_cents=cmd.balance_cents,
                note=clean_text(cmd.note) if cmd.note else None,
                gross_balance_cents=cmd.gross_balance_cents,
                holding_id=holding.id,
            )
            uow.anchors.upsert(anchor)
            uow.commit()
        difference = None if computed is None else cmd.balance_cents - computed
        return RecordHoldingValuationResult(anchor, computed, difference)


@dataclass(frozen=True)
class RedeemHoldingCommand:
    holding_id: str
    redeemed_on: dt.date
    amount_cents: int  # what the institution paid out (net)
    to_account_id: str | None = None  # checking account that received it (None: not tracked)


class RedeemHolding:
    """A withdrawal plus ``status=redeemed`` and a zero valuation, atomically (9.6).

    The zero valuation makes the yield of the whole holding come out right: payout minus what
    was put in, with no tax computed.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RedeemHoldingCommand) -> list[Transaction]:
        with self._uow as uow:
            holding = found(uow.holdings.get(cmd.holding_id), "holding")
            _holdings_account(uow, holding.account_id)
            if holding.status is HoldingStatus.REDEEMED:
                raise DomainError("HOLDING_REDEEMED")
            _require_checking(uow, cmd.to_account_id)
            last_valuation = max(
                (a.on_date for a in uow.anchors.list_for_holding(holding.id)),
                default=holding.applied_on,
            )
            if cmd.redeemed_on < max(holding.applied_on, last_valuation):
                raise DomainError("INVALID_REDEMPTION_DATE")
            legs = build_transfer_legs(
                uow,
                RegisterTransferCommand(
                    from_account_id=holding.account_id,
                    to_account_id=cmd.to_account_id,
                    posted_on=cmd.redeemed_on,
                    amount_cents=cmd.amount_cents,
                    holding_id=holding.id,
                ),
            )
            uow.transactions.add_many(legs)
            uow.anchors.upsert(
                BalanceAnchor(
                    new_id(), holding.account_id, cmd.redeemed_on, 0, None, None, holding.id
                )
            )
            uow.holdings.update(replace(holding, status=HoldingStatus.REDEEMED))
            uow.commit()
        return legs


@dataclass(frozen=True)
class RecordHoldingSnapshotCommand:
    """The gross position of one note on a reference date (an upsert per holding and date)."""

    holding_id: str
    as_of_date: dt.date
    gross_value_cents: int
    note: str | None = None


class RecordHoldingSnapshot:
    """Record (or correct) a dated snapshot of a holding; the current value follows the latest one.

    A snapshot is a holding valuation (``balance_anchors`` with ``holding_id``): there is one per
    holding and day, so a second one on the same day replaces the first in place. The value typed is
    the gross position; the net redemption value is taken as the same figure (the system never
    estimates tax), so totals that use the net value keep working.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: RecordHoldingSnapshotCommand) -> RecordHoldingValuationResult:
        if cmd.gross_value_cents < 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        return RecordHoldingValuation(self._uow).execute(
            RecordHoldingValuationCommand(
                cmd.holding_id,
                cmd.as_of_date,
                cmd.gross_value_cents,
                cmd.gross_value_cents,
                cmd.note,
            )
        )


class DeleteHoldingSnapshot:
    """Remove one wrong snapshot of a holding; the others stay."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, holding_id: str, anchor_id: str) -> None:
        with self._uow as uow:
            found(uow.holdings.get(holding_id), "holding")
            mine = {a.id for a in uow.anchors.list_for_holding(holding_id)}
            if anchor_id not in mine:
                raise DomainError("NOT_FOUND", entity="snapshot")
            uow.anchors.delete(anchor_id)
            uow.commit()
