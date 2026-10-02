"""``build_letter(facts) -> Letter``: pure, deterministic, no wording.

Definitions (each one has a test):
- ``despesas`` = expenses minus refunds; ``saldo`` = income - despesas (the month's balance);
  ``poupanca`` = saldo ÷ income (absent without income).
- Average = mean of the net expenses of the (up to three) months before, counting only months that
  have entries, rounded to the cent; the month is "below", "above" or "equal" to it.
- Over the goal: expense categories with a monthly goal whose spending in the month is strictly
  above it, biggest excess first. Severity: up to 10% above the goal "slight", from 50% "far".
- Recurring share = recurring expenses ÷ gross expenses; the top categories' share likewise.
- Ahead (as of today): unpaid closed statements by due date, cash in checking accounts, cash after
  paying them, future installments. Cash unknown stays unknown (never zero).
- To-dos: statement differences, uncategorized entries of the month, accounts without a balance or
  valuation, stale valuations, an old or missing backup.
"""

import hashlib
from collections.abc import Mapping

from financas.application.letter.facts import LetterFacts
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
from financas.application.queries.summary import Summary
from financas.domain.money import YearMonth

MAX_LISTED_STATEMENTS = 3
MAX_TODOS = 6
SLIGHT = 1.10
FAR = 1.5


def _money(cents: int) -> Slot:
    return Slot(SlotKind.MONEY, abs(cents))


def _signed(cents: int) -> Slot:
    return Slot(SlotKind.SIGNED_MONEY, cents)


def _count(n: int) -> Slot:
    return Slot(SlotKind.COUNT, n)


def _name(text: str) -> Slot:
    return Slot(SlotKind.NAME, text)


def _percent(ratio: float) -> Slot:
    return Slot(SlotKind.PERCENT, ratio)


def _signal(code: str) -> Slot:
    return Slot(SlotKind.SIGNAL, code)


def _average(values: list[int]) -> int:
    n = len(values)
    return (sum(values) * 2 + n) // (2 * n) if n else 0


def _month_of(summary: Summary) -> YearMonth:
    return YearMonth.from_date(summary.period.start)


def _spent(summary: Summary, category_id: str) -> int:
    return next((r.total_cents for r in summary.by_category if r.category_id == category_id), 0)


def _sentence(code: str, slots: Mapping[str, Slot], note: Note | None = None) -> Sentence:
    return Sentence(code, slots, note)


def _panorama(f: LetterFacts, average: int | None, months: list[YearMonth]) -> Section:
    s = f.summary
    if s.entry_count == 0:
        return Section(
            "panorama", (_sentence("panorama.empty", {"mes": Slot(SlotKind.MONTH, f.month)}),)
        )
    income, spent, balance = s.income_cents, s.net_expenses_cents, s.balance_cents
    if income == 0:
        opening = "panorama.no_income"
    elif balance > 0:
        opening = "panorama.surplus"
    elif balance < 0:
        opening = "panorama.deficit"
    else:
        opening = "panorama.even"
    slots: dict[str, Slot] = {
        "mes": Slot(SlotKind.MONTH, f.month),
        "receitas": _money(income),
        "despesas": _money(spent),
        "saldo": _money(balance),
    }
    note_slots: dict[str, Slot] = {
        "receitas": _money(income),
        "despesas": _money(spent),
        "saldo": _signed(balance),
    }
    if s.refunds_cents:
        note_slots["despesas_brutas"] = _money(s.expenses_cents)
        note_slots["estornos"] = _money(s.refunds_cents)
    if s.savings_rate is not None:
        slots["poupanca"] = _percent(abs(s.savings_rate))
        note_slots["poupanca"] = _percent(s.savings_rate)
    sentences = [_sentence(opening, slots, Note("balance", note_slots))]
    if average is not None:
        diff = average - spent
        signal = "equal" if diff == 0 else ("below" if diff > 0 else "above")
        avg_slots: dict[str, Slot] = {
            "despesas": _money(spent),
            "diferenca": _money(diff),
            "comparacao": _signal(signal),
            "meses": Slot(SlotKind.MONTHS, tuple(months)),
        }
        note: dict[str, Slot] = {
            "n": _count(len(months)),
            "media": _money(average),
            "despesas": _money(spent),
            "diferenca": _signed(diff),
            "meses": Slot(SlotKind.MONTHS, tuple(months)),
        }
        for i, p in enumerate(p for p in f.previous if p.entry_count > 0):
            note[f"m_{i + 1}"] = _money(p.net_expenses_cents)
        code = "panorama.average_equal" if diff == 0 else "panorama.average"
        sentences.append(_sentence(code, avg_slots, Note("average", note)))
    return Section("panorama", tuple(sentences))


def _where(f: LetterFacts) -> tuple[Section, tuple[CategoryBar, ...]]:
    s = f.summary
    if s.expenses_cents == 0:
        return Section(
            "where", (_sentence("where.none", {"mes": Slot(SlotKind.MONTH, f.month)}),)
        ), ()

    def name(category_id: str) -> str:
        info = f.categories.get(category_id)
        return info.name if info else category_id

    top = s.by_category[:2]
    bars = tuple(
        CategoryBar(r.category_id, name(r.category_id), r.total_cents) for r in s.by_category[:3]
    )
    total = sum(r.total_cents for r in top)
    share = total / s.expenses_cents
    slots: dict[str, Slot] = {
        "categoria_1": _name(name(top[0].category_id)),
        "valor_1": _money(top[0].total_cents),
        "soma_top": _money(total),
        "pct_top": _percent(share),
    }
    if len(top) == 2:
        slots["categoria_2"] = _name(name(top[1].category_id))
        slots["valor_2"] = _money(top[1].total_cents)
    note = Note("top", {**slots, "n": _count(len(top))})
    sentences = [_sentence("where.top2" if len(top) == 2 else "where.top1", slots, note)]
    if s.recurring_expenses_cents > 0:
        rec: dict[str, Slot] = {
            "recorrentes": _money(s.recurring_expenses_cents),
            "pct_rec": _percent(s.recurring_expenses_cents / s.expenses_cents),
        }
        code = "where.recurring"
        if f.recurring_count:
            rec["n_rec"] = _count(f.recurring_count)
            code = "where.recurring_one" if f.recurring_count == 1 else "where.recurring_count"
        sentences.append(
            _sentence(code, rec, Note("recurring", {**rec, "despesas": _money(s.expenses_cents)}))
        )
    return Section("where", tuple(sentences)), bars


def _agreed(f: LetterFacts) -> tuple[Section, GoalChart | None]:
    goals = {c.id: c.goal_cents for c in f.categories.values() if c.goal_cents}
    if not goals:
        return Section("agreed", (_sentence("agreed.no_goals", {}),)), None
    over = sorted(
        (
            (category_id, spent, goal)
            for category_id, goal in goals.items()
            if (spent := _spent(f.summary, category_id)) > goal
        ),
        key=lambda item: (-(item[1] - item[2]), item[0]),
    )
    if not over:
        slots = {"n_meta": _count(len(goals)), "mes": Slot(SlotKind.MONTH, f.month)}
        return Section("agreed", (_sentence("agreed.within", slots),)), None
    category_id, spent, goal = over[0]
    ratio = spent / goal
    severity = "slight" if ratio <= SLIGHT else ("far" if ratio >= FAR else "over")
    name = f.categories[category_id].name
    first: dict[str, Slot] = {
        "categoria": _name(name),
        "gasto": _money(spent),
        "meta": _money(goal),
        "excesso": _money(spent - goal),
        "severidade": _signal(severity),
        "mes": Slot(SlotKind.MONTH, f.month),
    }
    sentences = [_sentence("agreed.first_over", first, Note("goal", first))]
    if len(over) > 1:
        rest = over[1:]
        others: dict[str, Slot] = {
            "n_outras": _count(len(rest)),
            "soma_excessos": _money(sum(s - g for _, s, g in rest)),
            "nomes": Slot(SlotKind.NAMES, tuple(f.categories[c].name for c, _, _ in rest[:3])),
        }
        sentences.append(_sentence("agreed.others", others, Note("others", others)))
    window = [*f.previous, f.summary][-3:]
    chart = GoalChart(
        category_id,
        name,
        goal,
        tuple(_month_of(w) for w in window),
        tuple(_spent(w, category_id) for w in window),
    )
    return Section("agreed", tuple(sentences)), chart


def _ahead(f: LetterFacts) -> tuple[Section, tuple[CashStep, ...]]:
    sentences: list[Sentence] = []
    closed = f.closed_statements
    total = sum(c.outstanding_cents for c in closed)
    steps: list[CashStep] = []
    if closed:
        slots: dict[str, Slot] = {"n_faturas": _count(len(closed)), "faturas_total": _money(total)}
        note_slots: dict[str, Slot] = {"total": _money(total), "n": _count(len(closed))}
        for i, c in enumerate(closed[:MAX_LISTED_STATEMENTS], start=1):
            note_slots[f"nome_{i}"] = _name(c.card_name)
            note_slots[f"vence_{i}"] = Slot(SlotKind.DATE, c.due_date)
            note_slots[f"valor_{i}"] = _money(c.outstanding_cents)
        sentences.append(_sentence("ahead.statements", slots, Note("statements", note_slots)))
        for c in closed[:MAX_LISTED_STATEMENTS]:
            sentences.append(
                _sentence(
                    "ahead.due",
                    {
                        "nome": _name(c.card_name),
                        "vence": Slot(SlotKind.DATE, c.due_date),
                        "valor": _money(c.outstanding_cents),
                    },
                )
            )
        if len(closed) > MAX_LISTED_STATEMENTS:
            sentences.append(
                _sentence("ahead.more", {"n_mais": _count(len(closed) - MAX_LISTED_STATEMENTS)})
            )
    else:
        sentences.append(_sentence("ahead.no_statements", {}))
    cash = f.cash_cents
    if cash is None:
        sentences.append(_sentence("ahead.cash_unknown", {}))
    else:
        steps.append(CashStep("today", None, f.today, cash))
        running = cash
        for c in closed:
            running -= c.outstanding_cents
            steps.append(CashStep("after_statement", c.card_name, c.due_date, running))
        if closed:
            left = cash - total
            slots = {"caixa": _money(cash), "faturas_total": _money(total), "sobra": _money(left)}
            note = Note("cash", {**slots, "sobra": _signed(left)})
            code = "ahead.cash_covers" if left >= 0 else "ahead.cash_short"
            sentences.append(_sentence(code, slots, note))
        else:
            sentences.append(_sentence("ahead.cash_only", {"caixa": _money(cash)}))
    if f.future_installments_cents > 0:
        value = {"parcelas_futuras": _money(f.future_installments_cents)}
        sentences.append(_sentence("ahead.installments", value, Note("installments", value)))
    return Section("ahead", tuple(sentences)), tuple(steps)


def _todos(f: LetterFacts) -> tuple[Todo, ...]:
    todos: list[Todo] = []
    month = Slot(SlotKind.MONTH, f.month)
    for r in f.reconciliations:
        todos.append(
            Todo(
                "difference",
                {
                    "nome": _name(r.card_name),
                    "mes": Slot(SlotKind.MONTH, r.month),
                    "valor": _money(r.difference_cents),
                    "sentido": _signal(
                        "informed_higher" if r.difference_cents > 0 else "informed_lower"
                    ),
                },
                TodoTarget.STATEMENT,
                r.statement_id,
            )
        )
    if f.uncategorized_count > 0:
        todos.append(
            Todo(
                "uncategorized",
                {"n": _count(f.uncategorized_count), "mes": month},
                TodoTarget.UNCATEGORIZED_ENTRIES,
                f.uncategorized_category_id,
            )
        )
    for a in f.accounts_without_balance:
        todos.append(
            Todo(
                "no_valuation" if a.is_investment else "no_balance",
                {"nome": _name(a.name)},
                TodoTarget.INVESTMENTS if a.is_investment else TodoTarget.ACCOUNTS,
                a.account_id,
            )
        )
    for v in f.stale_valuations:
        todos.append(
            Todo(
                "stale_valuation",
                {"nome": _name(v.name), "dias": Slot(SlotKind.DAYS, v.age_days)},
                TodoTarget.INVESTMENTS,
                v.account_id,
            )
        )
    if f.backup_age_days is None:
        todos.append(Todo("backup_none", {}, TodoTarget.BACKUP))
    elif f.backup_age_days > f.backup_warn_days:
        todos.append(
            Todo("backup_old", {"dias": Slot(SlotKind.DAYS, f.backup_age_days)}, TodoTarget.BACKUP)
        )
    return tuple(todos)


def _before(todos: tuple[Todo, ...]) -> Section:
    if not todos:
        return Section("before", (_sentence("before.none", {}),))
    listed = todos[:MAX_TODOS]
    sentences = [_sentence("before.some", {"n_itens": _count(len(todos))})]
    sentences.extend(_sentence(f"before.item.{t.code}", t.slots) for t in listed)
    return Section("before", tuple(sentences))


def _fingerprint(f: LetterFacts) -> str:
    s = f.summary
    parts = [
        str(f.month),
        s.income_cents,
        s.expenses_cents,
        s.refunds_cents,
        s.recurring_expenses_cents,
        s.entry_count,
        sum(c.outstanding_cents for c in f.closed_statements),
        f.future_installments_cents,
        "" if f.cash_cents is None else f.cash_cents,
    ]
    digest = hashlib.sha256("|".join(map(str, parts)).encode("ascii")).hexdigest()
    return digest[:8].upper()


def build_letter(f: LetterFacts) -> Letter:
    """The letter of ``f.month`` as structured sections (see the module docstring)."""
    s = f.summary
    with_data = [p for p in f.previous if p.entry_count > 0]
    average = _average([p.net_expenses_cents for p in with_data]) if with_data else None
    months = [_month_of(p) for p in with_data]
    panorama = _panorama(f, average if s.entry_count else None, months)
    where, bars = _where(f)
    agreed, goal_chart = _agreed(f)
    ahead, steps = _ahead(f)
    todos = _todos(f)
    sections = (panorama, where, agreed, ahead, _before(todos))
    signal: str | None = None
    difference = 0
    if average is not None and s.entry_count:
        difference = abs(average - s.net_expenses_cents)
        signal = (
            "equal" if difference == 0 else ("below" if average > s.net_expenses_cents else "above")
        )
    spark = tuple(
        SparkBar(_month_of(p), p.net_expenses_cents, p.period.start == s.period.start)
        for p in f.year_to_date
    )
    signals = sum(
        1
        for sec in sections
        for sen in sec.sentences
        for v in sen.slots.values()
        if v.kind is SlotKind.SIGNAL
    )
    return Letter(
        month=f.month,
        written_on=f.today,
        has_entries=s.entry_count > 0,
        balance_cents=s.balance_cents,
        savings_rate=s.savings_rate,
        entry_count=s.entry_count,
        average_signal=signal,
        average_difference_cents=difference,
        sections=sections,
        spark=spark,
        spark_average_cents=average,
        top_categories=bars,
        goal_chart=goal_chart,
        cash_steps=steps,
        todos=todos,
        fingerprint=_fingerprint(f),
        signal_count=signals,
    )
