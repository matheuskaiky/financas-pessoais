"""Carta do mês: the monthly letter as structured parts (CLAUDE.md 16; docs/LLM_PLAN.md).

The letter is a list of sections; a section is a list of *sentences*. A sentence is a template
code (the pt-BR wording lives only in ``interfaces/messages/letter.py``) plus *slots*: named,
typed values computed from the user's data (``{receitas}``, ``{despesas}``, ``{categoria_1}``...),
and optionally a *note* that shows the calculation the system made. Qualitative phrases come from
deterministic signals ("abaixo da média", "passou do teto").

This structure is the contract with the future assistant: it will write the templates from the
same qualitative signals and the system will keep filling the slots and the notes. No number is
ever written by a model.
"""

from financas.application.letter.build import build_letter
from financas.application.letter.facts import (
    CategoryFacts,
    ClosedStatementFact,
    LetterFacts,
    PendingAccountFact,
    ReconciliationFact,
    StaleValuationFact,
)
from financas.application.letter.model import (
    CashStep,
    CategoryBar,
    GoalChart,
    Letter,
    Note,
    Section,
    Sentence,
    Slot,
    SlotKind,
    SparkBar,
    Todo,
    TodoTarget,
)

__all__ = [
    "CashStep",
    "CategoryBar",
    "CategoryFacts",
    "ClosedStatementFact",
    "GoalChart",
    "Letter",
    "LetterFacts",
    "Note",
    "PendingAccountFact",
    "ReconciliationFact",
    "Section",
    "Sentence",
    "Slot",
    "SlotKind",
    "SparkBar",
    "StaleValuationFact",
    "Todo",
    "TodoTarget",
    "build_letter",
]
