"""pt-BR catalogue: the only place where user-facing text for domain codes lives."""

from financas.domain.errors import DomainError
from financas.domain.models import AccountKind, CategoryGroup, CategoryKind, TransactionKind

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

ENTITY_LABELS: dict[str, str] = {
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
