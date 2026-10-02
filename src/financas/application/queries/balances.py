"""Account balances at a date (9.7). A checking account with no anchor is 'unavailable'."""

import datetime as dt
from dataclasses import dataclass

from financas.domain.models import AccountKind
from financas.domain.ports import UnitOfWork
from financas.domain.services.balances import AnchorPoint, balance_on


@dataclass(frozen=True)
class AccountBalance:
    account_id: str
    balance_cents: int | None  # None: no anchor yet ("saldo indisponível")
    last_anchor_date: dt.date | None


class ListAccountBalances:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, day: dt.date) -> list[AccountBalance]:
        with self._uow as uow:
            result: list[AccountBalance] = []
            for account in uow.accounts.list_all():
                if account.kind is AccountKind.CREDIT_CARD:
                    continue  # cards have statements and a limit, not a balance
                anchors = uow.anchors.list_for_account(account.id)
                points = [AnchorPoint(a.on_date, a.balance_cents) for a in anchors]
                balance = balance_on(points, uow.transactions.movements(account.id), day)
                last = max((a.on_date for a in anchors), default=None)
                result.append(AccountBalance(account.id, balance, last))
            return result
