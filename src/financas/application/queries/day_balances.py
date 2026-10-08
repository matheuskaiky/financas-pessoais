"""The account balance at the end of each day of the entries list ("Saldo da conta").

It is the running ledger balance, not the day's delta: the nearest informed balance plus every
movement (income, expense, refund and both transfer legs) up to the end of the day, exactly what a
bank statement shows. Unfiltered it is the sum of the checking accounts; filtered to one account,
that account's balance. An account with no informed balance has none ("saldo indisponível", never
0): when only some accounts have one the sum is flagged partial.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from financas.domain.models import AccountKind
from financas.domain.ports import UnitOfWork
from financas.domain.services.balances import AnchorPoint, balance_on


@dataclass(frozen=True)
class DayBalance:
    balance_cents: int | None  # ``None``: no checking account has an informed balance
    partial: bool = False  # some checking accounts have none and are left out of the sum


class GetDayBalances:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self, days: Iterable[dt.date], account_id: str | None = None
    ) -> dict[dt.date, DayBalance]:
        """``{day: balance at the end of it}``; empty when the list is not a checking account's."""
        with self._uow as uow:
            accounts = [
                a
                for a in uow.accounts.list_all()
                if a.kind is AccountKind.CHECKING and (account_id is None or a.id == account_id)
            ]
            ledgers = [
                (
                    [
                        AnchorPoint(x.on_date, x.balance_cents)
                        for x in uow.anchors.list_for_account(a.id)
                    ],
                    uow.transactions.movements(a.id),
                )
                for a in accounts
            ]
        result: dict[dt.date, DayBalance] = {}
        if not ledgers:
            return result
        for day in set(days):
            known = [v for v in (balance_on(a, m, day) for a, m in ledgers) if v is not None]
            result[day] = DayBalance(sum(known) if known else None, len(known) < len(ledgers))
        return result
