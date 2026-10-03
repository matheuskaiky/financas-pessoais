"""The ``--template`` text: the header plus one commented example row per kind."""

from financas.application.csvfeed.model import COLUMN_ORDER

TEMPLATE_DELIMITER = ";"

# One example per kind, in header order. Accounts and categories must exist in the database.
_EXAMPLES = (
    "2026-10-05;expense;Checking;;-45,90;Pharmacy;Health;no;;;;;;",
    "2026-10-05;income;Checking;;3.500,00;Salary October;Salary;yes;;;;;;",
    "2026-10-06;refund;Card;;12,50;Store refund;;;;2026-10;;;;",
    "2026-10-07;transfer;Checking;Savings;500,00;Monthly contribution;;;;;;;;",
    "2026-10-08;expense;Card;;1.200,00;Notebook;Shopping;;;;3;;total;",
    "2026-10-08;expense;Card;;100,00;Course (running);Education;;;2026-11;10;3;installment;",
    "2026-10-15;transfer;Checking;Card;850,00;Card payment;;;;2026-10;;;;",
    "2026-10-31;balance;Savings;;10.250,00;Month end;;;;;;;;10.400,00",
)


def template_text() -> str:
    """Header line and commented example rows (``# ...``), semicolon-delimited."""
    header = TEMPLATE_DELIMITER.join(c.value for c in COLUMN_ORDER)
    lines = [header]
    lines.extend(f"# {example}" for example in _EXAMPLES)
    return "\n".join(lines) + "\n"
