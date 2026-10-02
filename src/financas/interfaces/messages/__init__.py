"""pt-BR catalogue: the only place where user-facing text for domain codes lives."""

from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    CategoryGroup,
    CategoryKind,
    Statement,
    StatementStatus,
    TransactionKind,
)
from financas.domain.services.card_cycle import AssignmentReason, StatementAssignment
from financas.domain.services.statements import LimitAlert
from financas.interfaces.formatting import format_date, format_date_short, format_month

TRANSACTION_KIND_LABELS: dict[TransactionKind, str] = {
    TransactionKind.EXPENSE: "Despesa",
    TransactionKind.INCOME: "Receita",
    TransactionKind.REFUND: "Estorno",
    TransactionKind.TRANSFER: "Transferência",
}

CATEGORY_KIND_LABELS: dict[CategoryKind, str] = {
    CategoryKind.EXPENSE: "Despesa",
    CategoryKind.INCOME: "Receita",
    CategoryKind.NEUTRAL: "Neutra",
}

CATEGORY_GROUP_LABELS: dict[CategoryGroup, str] = {
    CategoryGroup.ESSENTIAL: "Essencial",
    CategoryGroup.NON_ESSENTIAL: "Não essencial",
    CategoryGroup.CHARGES: "Encargos",
    CategoryGroup.INCOME: "Renda",
    CategoryGroup.MOVEMENT: "Movimentação",
    CategoryGroup.REVIEW: "Revisar",
}

ACCOUNT_KIND_LABELS: dict[AccountKind, str] = {
    AccountKind.CHECKING: "Conta corrente",
    AccountKind.CREDIT_CARD: "Cartão de crédito",
    AccountKind.INVESTMENT: "Investimento",
}

STATEMENT_STATUS_LABELS: dict[StatementStatus, str] = {
    StatementStatus.FUTURE: "Futura",
    StatementStatus.OPEN: "Aberta",
    StatementStatus.CLOSED: "Fechada",
    StatementStatus.PAID: "Paga",
}

LIMIT_ALERT_LABELS: dict[LimitAlert, str] = {
    LimitAlert.NONE: "Normal",
    LimitAlert.WARNING: "Atenção · 80%+",
    LimitAlert.EXCEEDED: "Limite estourado",
    LimitAlert.NOT_INFORMED: "Limite não informado",
}

ENTITY_LABELS: dict[str, str] = {
    "statement": "Fatura",
    "plan": "Compra parcelada",
    "institution": "Instituição",
    "account": "Conta",
    "category": "Categoria",
    "transaction": "Lançamento",
}

ERROR_MESSAGES: dict[str, str] = {
    "INVALID_AMOUNT": "Valor inválido. Use o formato 1.234,56.",
    "INVALID_YEAR_MONTH": "Mês inválido. Use o formato AAAA-MM.",
    "INVALID_DATE": "Data inválida. Use dd/mm/aaaa.",
    "INVALID_COLOR": "Cor inválida. Use o formato #RRGGBB.",
    "INVALID_SLUG": "Não foi possível gerar um identificador a partir desse nome.",
    "SIGN_KIND_MISMATCH": "O sinal do valor não combina com o tipo de lançamento ({kind_label}).",
    "CATEGORY_KIND_MISMATCH": (
        "A categoria ({category_kind_label}) não combina com o tipo de lançamento ({kind_label})."
    ),
    "CATEGORY_GROUP_KIND_MISMATCH": (
        "O grupo ({group_label}) não combina com o tipo da categoria ({kind_label})."
    ),
    "TRANSFER_NEEDS_ACCOUNT": "Informe ao menos uma conta (origem ou destino) na transferência.",
    "TRANSFER_SAME_ACCOUNT": "Origem e destino da transferência precisam ser contas diferentes.",
    "USE_TRANSFER_FOR_TRANSFERS": "Transferências são lançadas pelo comando de transferência.",
    "AMOUNT_NOT_POSITIVE": "O valor precisa ser maior que zero.",
    "EMPTY_NAME": "Informe um nome.",
    "EMPTY_DESCRIPTION": "Informe uma descrição.",
    "DUPLICATE_SLUG": "Já existe um registro com o identificador “{slug}”.",
    "NOT_FOUND": "{entity_label} não encontrado(a).",
    "AMBIGUOUS_REFERENCE": "Mais de um registro corresponde a “{reference}”. Seja mais específico.",
    "ACCOUNT_INACTIVE": "Esta conta está desativada.",
    "ACCOUNT_KIND_NOT_ALLOWED": (
        "Este tipo de conta ({account_kind_label}) não aceita esse lançamento por enquanto."
    ),
    "IMAGE_TOO_LARGE": "A imagem é grande demais (máximo de {max_kb} KB).",
    "IMAGE_TYPE_NOT_ALLOWED": "Formato de imagem não permitido. Use PNG, JPEG ou WebP.",
    "IMAGE_NOT_SUPPORTED": "Este item não aceita imagem, apenas cor.",
    "BACKUP_NEEDS_SQLITE_FILE": "O backup exige um banco SQLite em arquivo.",
    "AMOUNT_REQUIRED": "Informe o valor total ou o valor da parcela (só um dos dois).",
    "BALANCE_NOT_FOR_CARDS": "Cartões não têm saldo informado; use a fatura e o limite.",
    "CARD_DAYS_REQUIRED": "Informe o dia de fechamento e o dia de vencimento do cartão.",
    "CARD_FIELDS_ONLY_FOR_CARDS": "Fechamento, vencimento e limite são só de cartões de crédito.",
    "CARD_REQUIRED": "Esta operação exige um cartão de crédito.",
    "DIFFERENCE_NOT_POSITIVE": (
        "Não há diferença a lançar: o total do banco não é maior que o total lançado."
    ),
    "INSTALLMENT_AMOUNT_TOO_SMALL": "O valor é pequeno demais para {count} parcelas.",
    "INSTALLMENT_OUT_OF_RANGE": (
        "Parcela atual {number} fora do intervalo: a compra tem {count} parcela(s)."
    ),
    "INVALID_CARD_DAY": "Dia inválido: use um dia de 1 a 31.",
    "INVALID_STATEMENT_DATES": "O vencimento precisa ser depois do fechamento.",
    "NOT_A_CARD_PURCHASE": "Este lançamento não é uma compra de cartão.",
    "NOTHING_TO_PAY": "Esta fatura não tem valor a pagar.",
    "PURCHASE_DATE_REQUIRED": "Informe a data da compra ou a fatura.",
    "STATEMENT_ALREADY_PAID": "A fatura já está paga; as parcelas dela não podem ser alteradas.",
    "STATEMENT_ONLY_FOR_CARDS": "Só lançamentos de cartão têm fatura.",
    "STATEMENT_REQUIRED": "Compra em andamento: informe a fatura da parcela atual.",
    "TOTAL_NOT_INFORMED": "Informe antes o total que o banco mostra para a fatura.",
}

FLASH_MESSAGES: dict[str, str] = {
    "entry": "Lançamento salvo.",
    "transfer": "Transferência salva.",
    "deleted": "Lançamento apagado.",
    "institution": "Instituição salva.",
    "account": "Conta salva.",
    "category": "Categoria salva.",
    "appearance": "Aparência atualizada.",
    "balance": "Saldo registrado.",
    "backup": "Backup criado. Guarde também uma cópia em outro disco ou dispositivo.",
    "card": "Cartão salvo.",
    "card_settings": "Cartão atualizado. Faturas já criadas mantêm as datas.",
    "purchase": "Compra salva. Todas as parcelas, inclusive as futuras, já foram geradas.",
    "payment": "Pagamento registrado.",
    "informed": "Total informado atualizado.",
    "difference": "Diferença lançada como “Não categorizado”.",
    "dates": "Datas da fatura atualizadas.",
    "adjusted": "Valor da parcela ajustado.",
    "plan_deleted": "Compra parcelada apagada.",
}

_FALLBACK = "Erro inesperado ({code})."


def render_error(error: DomainError) -> str:
    """Turn a domain error (code + params) into a pt-BR sentence."""
    params = dict(error.params)
    if "kind" in params:
        kind = str(params["kind"])
        if error.code in {"CATEGORY_GROUP_KIND_MISMATCH"}:
            params["kind_label"] = CATEGORY_KIND_LABELS[CategoryKind(kind)]
        else:
            params["kind_label"] = TRANSACTION_KIND_LABELS[TransactionKind(kind)]
    if "category_kind" in params:
        params["category_kind_label"] = CATEGORY_KIND_LABELS[
            CategoryKind(str(params["category_kind"]))
        ]
    if "group" in params:
        params["group_label"] = CATEGORY_GROUP_LABELS[CategoryGroup(str(params["group"]))]
    if error.code == "ACCOUNT_KIND_NOT_ALLOWED":
        params["account_kind_label"] = ACCOUNT_KIND_LABELS[AccountKind(str(params["account_kind"]))]
    if "entity" in params:
        params["entity_label"] = ENTITY_LABELS.get(str(params["entity"]), "Registro")
    if "max_bytes" in params:
        params["max_kb"] = int(params["max_bytes"]) // 1024
    template = ERROR_MESSAGES.get(error.code)
    if template is None:
        return _FALLBACK.format(code=error.code)
    return template.format(**params)


def statement_label(statement: Statement) -> str:
    """``Fatura jul/2026 · fecha 25/07 · vence 05/08``: closing and due dates always together."""
    closing = format_date_short(statement.closing_date)
    due = format_date_short(statement.due_date)
    return f"Fatura {format_month(statement.month)} · fecha {closing} · vence {due}"


def explain_assignment(a: StatementAssignment) -> str:
    """The reason a purchase went to a statement, in Portuguese (CLAUDE.md rule 5)."""
    where = (
        f"fatura de {format_month(a.month)} "
        f"(fecha {format_date_short(a.closing_date)} · vence {format_date_short(a.due_date)})"
    )
    if a.reason is AssignmentReason.EXPLICIT or a.purchase_date is None:
        return f"Fatura escolhida por você: {where}."
    when = format_date(a.purchase_date)
    if a.reason is AssignmentReason.BEFORE_CLOSING:
        return f"Compra em {when}, antes do fechamento do dia {a.closing_day} → {where}."
    if a.reason is AssignmentReason.ON_CLOSING_DAY:
        return (
            f"Compra em {when}, no dia do fechamento (dia {a.closing_day}), ainda entra → {where}."
        )
    return f"Compra em {when}, depois do fechamento do dia {a.closing_day} → {where}."


def best_day_hint(a: StatementAssignment) -> str:
    return (
        f"Melhor dia de compra neste cartão: dia {a.best_purchase_day} (logo depois do fechamento)."
    )
