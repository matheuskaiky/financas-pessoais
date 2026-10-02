"""E se…: a purchase simulator. Everything is computed from the user's data; nothing is saved."""

from financas.application.whatif.simulate import (
    CardFacts,
    CardScenario,
    CashScenario,
    MonthDue,
    Purchase,
    ScheduleLine,
    Simulation,
    WhatIfFacts,
    monthly_dues,
    simulate,
    tightest,
)

__all__ = [
    "CardFacts",
    "CardScenario",
    "CashScenario",
    "MonthDue",
    "Purchase",
    "ScheduleLine",
    "Simulation",
    "WhatIfFacts",
    "monthly_dues",
    "simulate",
    "tightest",
]
