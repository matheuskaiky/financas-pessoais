"""Review files of the spreadsheet import (CLAUDE.md 13.1): CSV in and out, and the report.

``plan`` writes five files under ``data/import/`` (ignored by git, they hold real descriptions):

* ``contas.csv``        accounts to create (cards with due day and closing days, opening balances);
* ``categorias.csv``    category of the old workbook -> slug;
* ``contrapartes.csv``  one decision per Pix counterparty (own / third_party / category:<slug>);
* ``lancamentos.csv``   every planned entry, with flags, ``keep`` and ``category_override`` columns;
* ``relatorio.txt``     counts and totals (no descriptions).

The files are edited by the user (Excel in pt-BR uses ``;``); the next ``plan`` or ``apply`` reads
them back, so a decision taken once is never asked again.
"""

import csv
import datetime as dt
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from financas.application.imports.model import (
    AccountSpec,
    CounterpartyLine,
    EntryAction,
    Flag,
    ImportConfig,
    ImportPlan,
    Issue,
    IssueLevel,
    LegacyWorkbook,
    Origin,
    PaymentAction,
    PlanAction,
    RowKind,
    TransferAction,
    TransferHint,
)
from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, AssetClass
from financas.domain.money import parse_brl
from financas.domain.services.text import normalize_search, slugify
from financas.interfaces.formatting import format_brl, format_decimal_comma, parse_date
from financas.interfaces.messages.imports import FLAG_LABELS, ISSUE_LABELS, REPORT

DELIMITER = ";"
ACCOUNTS_FILE = "contas.csv"
CATEGORIES_FILE = "categorias.csv"
COUNTERPARTIES_FILE = "contrapartes.csv"
ENTRIES_FILE = "lancamentos.csv"
REPORT_FILE = "relatorio.txt"

ACCOUNT_COLUMNS = (
    "key", "kind", "institution", "nickname", "legacy_institution", "due_day",
    "closes_before_due", "limit", "opening_balance", "opening_date", "asset_class",
    "institution_group",
)  # fmt: skip
ENTRY_COLUMNS = (
    "sheet_ids", "date", "action", "account", "kind", "category", "amount", "description",
    "statement", "flags", "keep", "category_override",
)  # fmt: skip

# legacy category name -> slug of the initial categories (names of the old workbook, not personal)
DEFAULT_CATEGORY_MAP = {
    "Alimentação": "food", "Restaurantes": "food", "Assinaturas": "subscriptions",
    "Compras": "shopping", "Serviços": "services", "Supermercado": "groceries",
    "Transporte": "transport", "Telecom": "telecom", "Telecomunicações": "telecom",
    "Seguros": "insurance", "Saúde": "health", "Saude/Farmacia": "health", "Drogaria": "health",
    "Educacao": "education", "Casa/Construcao": "home", "Construção": "home",
    "Encargos": "fees", "Tarifas/Encargos": "fees", "Impostos": "taxes", "Outros": "other",
    "Não categorizado": "uncategorized", "Receita": "other_income", "Salario": "salary",
    "Estorno": "refund", "Transferencia": "transfer",
}  # fmt: skip


# ---- low-level CSV ---------------------------------------------------------------------------


def _write(path: Path, columns: Iterable[str], rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), delimiter=DELIMITER)
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=DELIMITER)
        return [{k: (v or "").strip() for k, v in line.items() if k is not None} for line in reader]


# ---- accounts --------------------------------------------------------------------------------


def template_accounts(workbook: LegacyWorkbook) -> tuple[AccountSpec, ...]:
    """One account per institution/origin found in the rows (card settings left blank)."""
    nicknames = {
        (normalize_search(a.institution), normalize_search(a.origin)): a.nickname
        for a in workbook.accounts
        if a.nickname
    }
    specs: dict[str, AccountSpec] = {}
    for row in workbook.rows:
        card = row.origin is Origin.CARD
        key = f"{slugify(row.institution)}_{'card' if card else 'cc'}"
        if key in specs:
            continue
        origin_text = "cartao de credito" if card else "conta corrente"
        nickname = nicknames.get(
            (normalize_search(row.institution), origin_text),
            f"{row.institution} {'cartão' if card else 'c/c'}",
        )
        specs[key] = AccountSpec(
            key=key,
            kind=AccountKind.CREDIT_CARD if card else AccountKind.CHECKING,
            institution=row.institution,
            nickname=nickname,
            legacy_institution=row.institution,
        )
    sweep = [r for r in workbook.rows if r.transfer_hint is TransferHint.SWEEP]
    if sweep:
        bank = sweep[0].institution
        specs["sweep"] = AccountSpec(
            key="sweep",
            kind=AccountKind.INVESTMENT,
            institution=bank,
            nickname=f"{bank} Rende Fácil",
            asset_class=AssetClass.FIXED_INCOME,
        )
    return tuple(specs.values())


def write_accounts(path: Path, specs: Iterable[AccountSpec]) -> None:
    rows = [
        {
            "key": s.key,
            "kind": s.kind.value,
            "institution": s.institution,
            "nickname": s.nickname,
            "legacy_institution": s.legacy_institution or "",
            "due_day": str(s.due_day or ""),
            "closes_before_due": str(s.closes_before_due or ""),
            "limit": format_decimal_comma(s.limit_cents) if s.limit_cents is not None else "",
            "opening_balance": (
                format_decimal_comma(s.opening_cents) if s.opening_cents is not None else ""
            ),
            "opening_date": s.opening_on.isoformat() if s.opening_on else "",
            "asset_class": s.asset_class.value if s.asset_class else "",
            "institution_group": s.institution_group or "",
        }
        for s in specs
    ]
    _write(path, ACCOUNT_COLUMNS, rows)


def read_accounts(path: Path, today: dt.date) -> tuple[AccountSpec, ...]:
    specs = []
    for line in _read(path):
        try:
            specs.append(
                AccountSpec(
                    key=line["key"],
                    kind=AccountKind(line["kind"]),
                    institution=line["institution"],
                    nickname=line["nickname"],
                    legacy_institution=line["legacy_institution"] or None,
                    due_day=int(line["due_day"]) if line["due_day"] else None,
                    closes_before_due=(
                        int(line["closes_before_due"]) if line["closes_before_due"] else None
                    ),
                    limit_cents=parse_brl(line["limit"]) if line["limit"] else None,
                    opening_cents=(
                        parse_brl(line["opening_balance"]) if line["opening_balance"] else None
                    ),
                    opening_on=(
                        parse_date(line["opening_date"], today) if line["opening_date"] else None
                    ),
                    asset_class=AssetClass(line["asset_class"]) if line["asset_class"] else None,
                    institution_group=line["institution_group"] or None,
                )
            )
        except (KeyError, ValueError, DomainError) as error:
            raise DomainError("ACCOUNT_FILE_INVALID", file=path.name) from error
    return tuple(specs)


def account_issues(specs: Iterable[AccountSpec]) -> list[Issue]:
    return [
        Issue(IssueLevel.ERROR, "CARD_SETTINGS_MISSING", None, s.key)
        for s in specs
        if s.kind is AccountKind.CREDIT_CARD and (s.due_day is None or s.closes_before_due is None)
    ]


# ---- categories ------------------------------------------------------------------------------


def template_category_map(workbook: LegacyWorkbook) -> dict[str, str]:
    names = dict.fromkeys([*workbook.categories, *(r.category for r in workbook.rows)])
    normalized = {normalize_search(k): v for k, v in DEFAULT_CATEGORY_MAP.items()}
    return {name: normalized.get(normalize_search(name), "") for name in names if name}


def write_categories(path: Path, mapping: dict[str, str]) -> None:
    _write(
        path,
        ("legacy", "slug"),
        [{"legacy": k, "slug": v} for k, v in sorted(mapping.items())],
    )


def read_categories(path: Path) -> dict[str, str]:
    return {line["legacy"]: line["slug"] for line in _read(path) if line.get("slug")}


# ---- counterparties --------------------------------------------------------------------------


def write_counterparties(
    path: Path, lines: Iterable[CounterpartyLine], kept: dict[str, str]
) -> None:
    _write(
        path,
        ("key", "count", "out", "in", "suggested", "decision"),
        [
            {
                "key": c.key,
                "count": str(c.count),
                "out": format_decimal_comma(c.out_cents),
                "in": format_decimal_comma(c.in_cents),
                "suggested": c.suggested,
                # only what the user decided is kept here; blank means "follow the suggestion"
                "decision": kept.get(c.key, ""),
            }
            for c in lines
        ],
    )


def read_counterparty_decisions(path: Path) -> dict[str, str]:
    return {line["key"]: line["decision"] for line in _read(path) if line.get("decision")}


# ---- entries ---------------------------------------------------------------------------------


def _signed(kind: RowKind, cents: int) -> str:
    return format_decimal_comma(-cents if kind is RowKind.EXPENSE else cents)


def _flags(flags: Iterable[Flag]) -> str:
    return ",".join(f.value for f in flags)


def entry_rows(plan: ImportPlan) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for a in plan.actions:
        base = {"keep": "yes", "category_override": ""}
        if isinstance(a, EntryAction):
            rows.append(
                {
                    **base,
                    "sheet_ids": a.sheet_id,
                    "date": a.date.isoformat(),
                    "action": "entry",
                    "account": a.account_key,
                    "kind": a.kind.value,
                    "category": a.category_slug,
                    "amount": _signed(a.kind, a.amount_cents),
                    "description": a.description,
                    "statement": str(a.statement or ""),
                    "flags": _flags(a.flags),
                }
            )
        elif isinstance(a, TransferAction):
            rows.append(
                {
                    **base,
                    "sheet_ids": "+".join(a.sheet_ids),
                    "date": a.date.isoformat(),
                    "action": "transfer",
                    "account": f"{a.from_key or '-'}>{a.to_key or '-'}",
                    "kind": "transfer",
                    "category": "transfer",
                    "amount": format_decimal_comma(a.amount_cents),
                    "description": a.description,
                    "statement": "",
                    "flags": _flags(a.flags),
                }
            )
        elif isinstance(a, PaymentAction):
            rows.append(
                {
                    **base,
                    "sheet_ids": "+".join(a.sheet_ids),
                    "date": a.date.isoformat(),
                    "action": "payment",
                    "account": f"{a.from_key or '-'}>{a.card_key}",
                    "kind": "transfer",
                    "category": "transfer",
                    "amount": format_decimal_comma(a.amount_cents),
                    "description": "",
                    "statement": str(a.statement or ""),
                    "flags": _flags(a.flags),
                }
            )
        elif isinstance(a, PlanAction):
            numbers = f"{a.first_number}..{a.total_installments}"
            rows.append(
                {
                    **base,
                    "sheet_ids": "+".join(i.sheet_id for i in a.installments if i.sheet_id),
                    "date": (a.purchased_on or a.first_statement.day(1)).isoformat(),
                    "action": f"plan {numbers}",
                    "account": a.account_key,
                    "kind": "expense",
                    "category": a.category_slug,
                    "amount": format_decimal_comma(-a.installment_cents),
                    "description": a.description,
                    "statement": str(a.first_statement),
                    "flags": _flags(a.flags),
                }
            )
    return rows


def write_entries(path: Path, plan: ImportPlan, previous: dict[str, dict[str, str]]) -> None:
    """Writes the planned entries, keeping the user's ``keep``/override edits of a previous run."""
    rows = entry_rows(plan)
    for row in rows:
        old = previous.get(row["sheet_ids"])
        if old:
            row["keep"] = old.get("keep") or "yes"
            row["category_override"] = old.get("category_override", "")
    _write(path, ENTRY_COLUMNS, rows)


def read_entry_edits(path: Path) -> dict[str, dict[str, str]]:
    return {line["sheet_ids"]: line for line in _read(path) if line.get("sheet_ids")}


def apply_entry_edits(edits: dict[str, dict[str, str]]) -> tuple[frozenset[str], dict[str, str]]:
    """``(sheet ids to drop, category overrides by sheet id)`` from the reviewed entries file."""
    skip: set[str] = set()
    overrides: dict[str, str] = {}
    for ids, line in edits.items():
        parts = [i for i in ids.split("+") if i]
        if line.get("keep", "yes").lower() in {"no", "n", "nao", "não", "0"}:
            skip.update(parts)
        elif line.get("category_override") and len(parts) >= 1:
            overrides[parts[0]] = line["category_override"]
    return frozenset(skip), overrides


# ---- config assembly -------------------------------------------------------------------------


def build_config(
    *,
    year: int,
    accounts: tuple[AccountSpec, ...],
    category_map: dict[str, str],
    category_kinds: dict[str, str],
    aliases: Iterable[str],
    decisions: dict[str, str],
    edits: dict[str, dict[str, str]],
) -> ImportConfig:
    skip, overrides = apply_entry_edits(edits)
    sweep = next((a.key for a in accounts if a.kind is AccountKind.INVESTMENT), None)
    return ImportConfig(
        year=year,
        accounts=accounts,
        category_map=category_map,
        category_kinds=category_kinds,
        holder_aliases=tuple(normalize_search(a) for a in aliases if a.strip()),
        counterparty_decisions=decisions,
        skip_ids=skip,
        category_overrides=overrides,
        sweep_account_key=sweep,
    )


def with_account_issues(plan: ImportPlan) -> ImportPlan:
    extra = account_issues(plan.accounts)
    return replace(plan, issues=(*plan.issues, *extra)) if extra else plan


# ---- report ----------------------------------------------------------------------------------


def _account_totals(plan: ImportPlan) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for a in plan.actions:
        if isinstance(a, EntryAction):
            sign = -1 if a.kind is RowKind.EXPENSE else 1
            totals[a.account_key] += sign * a.amount_cents
        elif isinstance(a, TransferAction):
            if a.from_key:
                totals[a.from_key] -= a.amount_cents
            if a.to_key:
                totals[a.to_key] += a.amount_cents
        elif isinstance(a, PaymentAction):
            if a.from_key:
                totals[a.from_key] -= a.amount_cents
            totals[a.card_key] += a.amount_cents
        else:
            present = {i.number: i.amount_cents for i in a.installments}
            for n in range(a.first_number, a.total_installments + 1):
                totals[a.account_key] -= present.get(n, a.installment_cents)
    return dict(totals)


def render_report(plan: ImportPlan, year: int, directory: Path) -> str:
    entries = [a for a in plan.actions if isinstance(a, EntryAction)]
    transfers = [a for a in plan.actions if isinstance(a, TransferAction)]
    plans = [a for a in plan.actions if isinstance(a, PlanAction)]
    payments = [a for a in plan.actions if isinstance(a, PaymentAction)]
    present = sum(len(p.installments) for p in plans)
    generated = sum(p.total_installments - p.first_number + 1 for p in plans) - present
    flags = Counter(f for a in plan.actions for f in a.flags)
    lines = [REPORT["title"], ""]
    lines.append(
        REPORT["scope"].format(year=year, scope=plan.scope_rows, skipped=plan.skipped_rows)
    )
    lines.append(
        REPORT["actions"].format(
            entries=len(entries), transfers=len(transfers), plans=len(plans),
            present=present, generated=generated, payments=len(payments),
        )
    )  # fmt: skip
    lines += ["", REPORT["attention"]]
    lines += [f"  {n:>4} × {FLAG_LABELS[f]}" for f, n in sorted(flags.items())] or [
        f"  {REPORT['none']}"
    ]
    lines += ["", REPORT["issues"]]
    issue_count = Counter((i.level, i.code) for i in plan.issues)
    for (level, code), n in sorted(issue_count.items()):
        label = ISSUE_LABELS.get(code, code)
        examples = sorted({i.detail for i in plan.issues if i.code == code and i.detail})[:5]
        extra = f" ({', '.join(examples)})" if examples else ""
        lines.append(f"  [{level.value}] {n:>4} × {label}{extra}")
    if not issue_count:
        lines.append(f"  {REPORT['none']}")
    lines += ["", REPORT["accounts"]]
    names = {a.key: a.nickname for a in plan.accounts}
    for key, total in sorted(_account_totals(plan).items()):
        lines.append(f"  {names.get(key, key)}: {format_brl(total)}")
    own = sum(1 for c in plan.counterparties if c.decision == "own")
    third = sum(1 for c in plan.counterparties if c.decision == "third_party")
    lines += [
        "",
        REPORT["counterparties"].format(total=len(plan.counterparties), own=own, third=third),
        "",
        REPORT["next"].format(directory=directory),
    ]
    return "\n".join(lines) + "\n"
