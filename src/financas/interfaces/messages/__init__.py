"""pt-BR catalogue: the only place where user-facing text for domain codes lives."""

import datetime as dt

from financas.domain.errors import DomainError
from financas.domain.models import (
    AccountKind,
    AssetClass,
    CategoryGroup,
    CategoryKind,
    HoldingStatus,
    Indexer,
    InstrumentType,
    InvestmentTracking,
    Liquidity,
    RateMode,
    Statement,
    StatementStatus,
    TransactionKind,
)
from financas.domain.money import YearMonth, format_brl
from financas.domain.services.card_cycle import AssignmentReason, StatementAssignment
from financas.domain.services.countdown import Countdown, CountdownKind
from financas.domain.services.holdings import LiquidityBucket
from financas.domain.services.merchants import ChangeReason
from financas.domain.services.recurring import AlertKind, RecurringAlert
from financas.domain.services.statements import LimitAlert
from financas.interfaces.formatting import (
    format_bps,
    format_date,
    format_date_short,
    format_month,
)
from financas.interfaces.messages.csvfeed import CSV_ERROR_MESSAGES

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

ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.FIXED_INCOME: "Renda fixa",
    AssetClass.EQUITIES: "Ações",
    AssetClass.REAL_ESTATE_FUNDS: "Fundos imobiliários",
    AssetClass.CRYPTO: "Cripto",
    AssetClass.OTHER: "Outros",
}

INSTRUMENT_TYPE_LABELS: dict[InstrumentType, str] = {
    InstrumentType.CDB: "CDB",
    InstrumentType.LC: "LC",
    InstrumentType.LCI: "LCI",
    InstrumentType.LCA: "LCA",
    InstrumentType.CRI: "CRI",
    InstrumentType.CRA: "CRA",
    InstrumentType.DEBENTURE: "Debênture",
    InstrumentType.TREASURY_SELIC: "Tesouro Selic",
    InstrumentType.TREASURY_IPCA: "Tesouro IPCA+",
    InstrumentType.TREASURY_PREFIXED: "Tesouro Prefixado",
    InstrumentType.SAVINGS_ACCOUNT: "Poupança",
    InstrumentType.FUND: "Fundo",
    InstrumentType.PENSION: "Previdência",
    InstrumentType.STOCK: "Ação",
    InstrumentType.REIT: "FII",
    InstrumentType.ETF: "ETF",
    InstrumentType.CRYPTO: "Cripto",
    InstrumentType.OTHER: "Outro",
}

INDEXER_LABELS: dict[Indexer, str] = {
    Indexer.CDI: "CDI",
    Indexer.SELIC: "Selic",
    Indexer.IPCA: "IPCA",
    Indexer.PREFIXED: "Prefixado",
    Indexer.OTHER: "Outro índice",
}

RATE_MODE_LABELS: dict[RateMode, str] = {
    RateMode.PERCENT_OF_INDEX: "% do índice",
    RateMode.SPREAD_OVER_INDEX: "Índice + taxa",
    RateMode.FIXED_ANNUAL: "Taxa fixa ao ano",
}

LIQUIDITY_LABELS: dict[Liquidity, str] = {
    Liquidity.DAILY: "Diária",
    Liquidity.AT_MATURITY: "No vencimento",
}

LIQUIDITY_BUCKET_LABELS: dict[LiquidityBucket, str] = {
    LiquidityBucket.TODAY: "Disponível hoje",
    LiquidityBucket.WITHIN_30: "Até 30 dias",
    LiquidityBucket.WITHIN_90: "Até 90 dias",
    LiquidityBucket.WITHIN_180: "Até 180 dias",
    LiquidityBucket.WITHIN_365: "Até 365 dias",
    LiquidityBucket.LATER: "Mais tarde",
}

TRACKING_LABELS: dict[InvestmentTracking, str] = {
    InvestmentTracking.ACCOUNT: "Por conta",
    InvestmentTracking.HOLDINGS: "Por aplicação",
}

# what the switch itself says, so the choice is clear without reading the manual
TRACKING_OPTION_LABELS: dict[InvestmentTracking, str] = {
    InvestmentTracking.HOLDINGS: "Controle por Notas/Aplicações (CDBs, fundos e ativos)",
    InvestmentTracking.ACCOUNT: "Controle Global (saldo único da conta, sem detalhar ativos)",
}

HOLDING_STATUS_LABELS: dict[HoldingStatus, str] = {
    HoldingStatus.ACTIVE: "Ativa",
    HoldingStatus.REDEEMED: "Resgatada",
}

ALERT_KIND_LABELS: dict[AlertKind, str] = {
    AlertKind.DISAPPEARED: "Sumiu",
    AlertKind.CHANGED: "Mudou de valor",
    AlertKind.APPEARED: "Apareceu",
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
    "holding": "Aplicação",
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
    "IMPORT_READER_MISSING": ("Falta o leitor de planilhas. Instale com: uv sync --extra import"),
    "IMPORT_UNKNOWN_ORIGIN": "Planilha, linha {row}: origem desconhecida.",
    "ACCOUNT_FILE_INVALID": "{file}: há uma linha inválida (confira tipo, dias, valores e datas).",
    "IMPORT_RUN_PLAN_FIRST": "Rode `financas import plan` e revise os arquivos antes de aplicar.",
    "IMPORT_PLAN_HAS_ERRORS": "O plano tem erros: corrija os arquivos de revisão e rode o plano.",
    "IMPORT_DATABASE_NOT_EMPTY": "A importação só roda num banco sem lançamentos.",
    "IMPORT_ACCOUNT_KIND_CONFLICT": "A conta {account} já existe com outro tipo.",
    "IMPORT_PAYMENT_WITHOUT_STATEMENT": "Pagamento de fatura sem fatura correspondente.",
    "IMPORT_UNKNOWN_KIND": "Planilha, linha {row}: tipo desconhecido.",
    "IMPORT_AMOUNT_NOT_CENTS": "Planilha, linha {row}: o valor não tem exatamente dois decimais.",
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
    "NOT_FOUND": "{entity_label}: registro não encontrado.",
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
    "OPENING_BALANCE_INCOMPLETE": "Para informar o saldo inicial, preencha o valor e a data.",
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
    "INVALID_DAYS_BEFORE_DUE": (
        "Informe de 1 a 27 dias: quantos dias antes do vencimento a fatura fecha."
    ),
    "NO_STATEMENT_FOR_MONTH": "Não foi possível calcular a fatura deste mês.",
    "INVALID_CARD_DAY": "Dia inválido: use um dia de 1 a 31.",
    "INVALID_STATEMENT_DATES": "O vencimento precisa ser depois do fechamento.",
    "NOT_A_CARD_PURCHASE": "Este lançamento não é uma compra de cartão.",
    "NOTHING_TO_PAY": "Esta fatura não tem valor a pagar.",
    "PURCHASE_DATE_REQUIRED": "Informe a data da compra ou a fatura.",
    "STATEMENT_ALREADY_PAID": "A fatura já está paga; as parcelas dela não podem ser alteradas.",
    "STATEMENT_ONLY_FOR_CARDS": "Só lançamentos de cartão têm fatura.",
    "STATEMENT_REQUIRED": "Compra em andamento: informe a fatura da parcela atual.",
    "TOTAL_NOT_INFORMED": "Informe antes o total que o banco mostra para a fatura.",
    "GROSS_ONLY_FOR_INVESTMENTS": "O valor bruto só se aplica a contas de investimento.",
    "INVALID_GROSS_BALANCE": "O valor bruto não pode ser menor que o valor líquido.",
    "INVESTMENT_FIELDS_ONLY_FOR_INVESTMENTS": (
        "Classe de ativo e reserva de emergência só existem em contas de investimento."
    ),
    "INVESTMENT_REQUIRED": "Esta operação exige uma conta de investimento.",
    "INVALID_DAYS": "O número de dias até o vencimento não pode ser negativo.",
    "INVALID_RATE": (
        "Taxa inválida: informe o tipo e o valor, e o índice quando a taxa depende dele."
    ),
    "MATURITY_REQUIRED": "Informe o vencimento: com liquidez no vencimento ele é obrigatório.",
    "INVALID_MATURITY": "O vencimento precisa ser depois da data de aplicação.",
    "INVALID_LIQUIDITY": (
        "Liquidez inválida: a carência só vale para liquidez diária e não pode "
        "ser antes da aplicação."
    ),
    "HOLDING_REDEEMED": "Esta aplicação já foi resgatada.",
    "HOLDING_REQUIRED": "Esta conta é controlada por aplicação: escolha a aplicação.",
    "HOLDING_NOT_IN_ACCOUNT": "A aplicação escolhida não pertence a esta conta.",
    "ACCOUNT_NOT_HOLDINGS_LEVEL": "Esta conta é controlada por conta, não por aplicação.",
    "ACCOUNT_TRACKS_HOLDINGS": (
        "Esta conta é controlada por aplicação: informe o saldo de cada aplicação."
    ),
    "TOTAL_REQUIRED": "Informe o total da fatura (ou use --clear para remover o total informado).",
    "INVALID_CHOICE": "Opção inválida. Escolha um dos valores da lista.",
    "INVALID_NUMBER": "Número inválido.",
    "INVALID_INSTALLMENT_COUNT": "Número de parcelas inválido: use um número de 1 a 120.",
    "ACCOUNT_REQUIRED": "Escolha a conta.",
    "INVALID_REDEMPTION_DATE": (
        "A data do resgate não pode ser antes da aplicação nem da última avaliação."
    ),
    "AMOUNT_TOO_LARGE": "O valor é grande demais.",
    "BUDGET_ONLY_FOR_EXPENSES": "Só categorias de despesa têm meta de orçamento.",
    "CARD_HAS_NO_DAILY_FLOW": "Cartões não têm fluxo diário: veja a fatura.",
    "USE_DELETE_PURCHASE": (
        "Esta parcela faz parte de uma compra parcelada: apague a compra inteira."
    ),
    "DUPLICATE_NAME": "Já existe uma categoria com esse nome.",
    "DUPLICATE_ACCOUNT_NAME": "Já existe uma conta ou um cartão com esse nome.",
    "NOTHING_TO_SAVE": "Informe o estabelecimento ou escolha outra categoria; ou use Pular.",
    "REVIEW_ONLY_FOR_EXPENSES": "A revisão rápida só vale para despesas.",
    "NOT_A_STATEMENT_PAYMENT": "Este lançamento não é um pagamento de fatura.",
    "MERCHANT_TOO_LONG": "O estabelecimento pode ter no máximo {max_length} caracteres.",
    "SPLIT_NEEDS_TWO_ITEMS": "Use pelo menos dois itens para dividir o lançamento.",
    "SPLIT_SUM_MISMATCH": "Os itens precisam somar o valor do lançamento: {remaining_text}.",
    "PARENT_CATEGORY_FORBIDDEN_WITH_SPLITS": (
        "Um lançamento dividido em itens não tem categoria própria: "
        "escolha a categoria de cada item."
    ),
    "SPLIT_AMOUNT_NEEDS_ITEMS": (
        "Este lançamento está dividido em itens: mude o valor e os itens juntos, no formulário."
    ),
    "SPLIT_ONLY_FOR_EXPENSES": "Só despesas podem ser divididas em itens.",
    "SPLIT_NOT_FOR_INSTALLMENTS": (
        "Parcelas de uma compra parcelada não podem ser divididas em itens."
    ),
    "MERGE_NEEDS_TWO": "Escolha pelo menos dois lançamentos para mesclar.",
    "MERGE_ONLY_PLAIN_EXPENSES": (
        "Só despesas comuns podem ser mescladas: sem parcelas, transferências ou estornos."
    ),
    "MERGE_ACCOUNT_MISMATCH": "Os lançamentos precisam ser da mesma conta ou do mesmo cartão.",
    "MERGE_STATEMENT_MISMATCH": "Os lançamentos do cartão precisam estar na mesma fatura.",
    "INVALID_PAYMENT_METHOD": "Forma de pagamento inválida para este lançamento ou esta conta.",
    "MERGE_ITEMIZED_FORBIDDEN": "Lançamento com itens não pode ser mesclado.",
    "MERGE_OUTSIDE_CURRENT_MONTH": (
        "Apenas compras do mês atual podem ser mescladas. "
        "Lançamentos de meses anteriores já estão consolidados."
    ),
    "DELETE_OUTSIDE_CURRENT_MONTH": (
        "Só lançamentos do mês atual podem ser apagados em lote. "
        "Os de outros meses são apagados um a um."
    ),
    "BAD_SELECTION_ID": "A seleção contém um lançamento inválido.",
    "TOO_MANY_IDS": "Selecione até {max} lançamentos por vez.",
    "REFUND_ONLY_FOR_EXPENSES": "Só despesas e compras podem ser marcadas como estornadas.",
    "NOTHING_TO_DELETE": (
        "Todas as parcelas deste parcelamento estão em faturas pagas e ficam no histórico."
    ),
    "TRANSFER_NOT_EDITABLE": (
        "Esta transferência não pode ser editada aqui: pagamentos de fatura e movimentos de "
        "aplicações têm tela própria. Para corrigir, apague e lance de novo."
    ),
    "NEUTRAL_NEEDS_EXPENSE_OR_INCOME": (
        "Só categorias de despesa ou de receita podem ser neutras: as de movimentação já ficam "
        "fora dos totais."
    ),
    "TRANSFER_SIDES_CHANGED": (
        "Uma transferência não pode ganhar nem perder uma conta controlada: "
        "apague e lance de novo para corrigir."
    ),
    "INSTALLMENT_FIELD_LOCKED": (
        "Data e conta de uma parcela seguem a fatura e o cartão da compra: só descrição, "
        "categoria, valor e observações podem ser alterados."
    ),
    "FUTURE_PAYMENT_FORBIDDEN": "Pagamentos de fatura não podem ter data futura.",
    "PAYMENT_DATE_BEFORE_PREVIOUS_DUE": (
        "A data do pagamento não pode ser anterior ao vencimento da fatura anterior "
        "({min_date_text})."
    ),
    "PURCHASE_DATE_LOCKED": (
        "A primeira parcela já está em uma fatura fechada ou paga: a data da compra é histórico "
        "e não pode mais ser alterada."
    ),
    "PLAN_TARGET_STATEMENT_CLOSED": (
        "Com a nova data, a parcela {number} cairia em uma fatura que já fechou. "
        "Escolha uma data em que as parcelas pendentes caiam em faturas abertas ou futuras."
    ),
    "ACCOUNT_KIND_CHANGE_NOT_ALLOWED": (
        "Só é possível trocar por outra conta do mesmo tipo (conta corrente por conta corrente, "
        "cartão por cartão)."
    ),
    "STATEMENT_CLOSED_NEEDS_ACK": (
        "A fatura deste lançamento já foi fechada. Marque “Estou ciente de que a fatura já está "
        "fechada” para salvar."
    ),
    "NOTHING_TO_ANTICIPATE": (
        "Nenhuma parcela pode ser antecipada: as que faltam já estão na fatura aberta, em fatura "
        "fechada ou paga."
    ),
    "INSTALLMENT_NOT_ANTICIPABLE": "A parcela {number} não pode ser antecipada.",
    "NO_OPEN_STATEMENT": "O cartão não tem uma fatura aberta para receber as parcelas.",
    "INVALID_DISCOUNT": (
        "O desconto precisa ser maior ou igual a zero e menor que o valor das parcelas."
    ),
    "DISCOUNT_RATE_OR_AMOUNT": "Informe a taxa de desconto ou o valor do desconto, não os dois.",
    "TRACKING_HAS_HOLDINGS": (
        "Esta conta possui {count} aplicação(ões) ativa(s). Para alternar para Controle Global, "
        "é necessário encerrar ou excluir as aplicações existentes primeiro (ou marcar “Arquivar "
        "as aplicações e trocar”, que as guarda no histórico, fora dos totais)."
    ),
    "TRACKING_HAS_VALUATIONS": (
        "Esta conta possui {count} avaliação(ões) de saldo da conta inteira. Ao passar para "
        "Controle por Notas elas ficam guardadas no histórico, mas deixam de contar: marque "
        "“Trocar mesmo assim” para confirmar."
    ),
}

ERROR_MESSAGES.update(CSV_ERROR_MESSAGES)  # codes that only the CSV feed raises (13.3)

BACKFILL_REASON_LABELS: dict[ChangeReason, str] = {
    ChangeReason.SUFFIX: "pelo sufixo da descrição",
    ChangeReason.ALIAS: "por um nome conhecido na descrição",
    ChangeReason.LEARNED: "pelo que você já usou antes",
    ChangeReason.CANONICALIZED: "nome padronizado",
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
    "card_details": "Nome e cor do cartão atualizados.",
    "purchase": "Compra salva. Todas as parcelas, inclusive as futuras, já foram geradas.",
    "payment": "Pagamento registrado.",
    "informed": "Total informado atualizado.",
    "difference": "Diferença lançada como “Não categorizado”.",
    "dates": "Datas da fatura atualizadas.",
    "adjusted": "Valor da parcela ajustado.",
    "plan_deleted": "Compra parcelada apagada.",
    "plan_pending_deleted": (
        "Parcelas pendentes apagadas. As de faturas já pagas ficaram no histórico."
    ),
    "entry_updated": "Lançamento atualizado.",
    "transfer_updated": "Transferência atualizada nas duas contas.",
    "category_renamed": "Categoria renomeada.",
    "payment_updated": "Pagamento atualizado. Saldos e situação da fatura foram recalculados.",
    "merged": "Lançamentos mesclados em um só. Cada item continua na sua categoria.",
    "anticipated": "Parcelas antecipadas para a fatura aberta.",
    "budget": "Metas atualizadas.",
    "valuation": "Avaliação registrada.",
    "snapshot_deleted": "Registro de saldo removido.",
    "category_neutral": "Categoria atualizada.",
    "valuation_yield": "Avaliação registrada.",
    "flow": "Movimentação registrada.",
    "investment_settings": "Conta de investimento atualizada.",
    "holding": "Aplicação cadastrada.",
    "holding_flags": "Aplicação atualizada.",
    "redeemed": "Aplicação resgatada.",
}

# errors whose sentence needs no parameters: safe to show from a code in the URL (?err=CODE)
PARAMETERLESS_ERRORS = frozenset(
    {
        "STATEMENT_ALREADY_PAID",
        "NOTHING_TO_DELETE",
        "MERGE_NEEDS_TWO",
        "MERGE_ONLY_PLAIN_EXPENSES",
        "MERGE_ACCOUNT_MISMATCH",
        "MERGE_STATEMENT_MISMATCH",
        "INVALID_PAYMENT_METHOD",
        "MERGE_ITEMIZED_FORBIDDEN",
        "MERGE_OUTSIDE_CURRENT_MONTH",
        "DELETE_OUTSIDE_CURRENT_MONTH",
        "BAD_SELECTION_ID",
        "STATEMENT_CLOSED_NEEDS_ACK",
        "USE_DELETE_PURCHASE",
        "NOT_A_CARD_PURCHASE",
        "HOLDING_REDEEMED",
    }
)

_FALLBACK = "Erro inesperado ({code})."


def render_error(error: DomainError) -> str:
    """Turn a domain error (code + params) into a pt-BR sentence."""
    params = dict(error.params)
    if "kind" in params:
        kind = str(params["kind"])
        if kind == "balance":  # a CSV feed kind that is not a transaction kind
            params["kind_label"] = "Saldo"
        elif error.code in {"CATEGORY_GROUP_KIND_MISMATCH"}:
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
    if "min_date" in params:
        params["min_date_text"] = format_date(dt.date.fromisoformat(str(params["min_date"])))
    if "remaining_cents" in params:
        remaining = int(params["remaining_cents"])
        params["remaining_text"] = (
            f"restam {format_brl(remaining)}"
            if remaining > 0
            else f"ultrapassou {format_brl(-remaining)}"
        )
    if "sum_cents" in params:
        params["sum_text"] = format_brl(int(params["sum_cents"]))
    if "total_cents" in params:
        params["total_text"] = format_brl(int(params["total_cents"]))
    if "max_bytes" in params:
        params["max_kb"] = int(params["max_bytes"]) // 1024
    template = ERROR_MESSAGES.get(error.code)
    if template is None:
        return _FALLBACK.format(code=error.code)
    return template.format(**params)


FAILURE_KIND_LABELS = {
    "server_error": "Erro no servidor",
    "http_error": "Página ou ação não encontrada",
    "js_error": "Erro na tela (JavaScript)",
    "unhandled_rejection": "Falha numa ação da tela",
    "htmx_error": "A tela não conseguiu falar com o servidor",
    "cli_error": "Erro num comando",
}


def statement_label(statement: Statement) -> str:
    """``Fatura jul/2026 · fecha 25/07 · vence 05/08``: closing and due dates always together."""
    closing = format_date_short(statement.closing_date)
    due = format_date_short(statement.due_date)
    return f"Fatura {format_month(statement.month)} · fecha {closing} · vence {due}"


def _days(n: int) -> str:
    return f"{n} dia" if n == 1 else f"{n} dias"


def closing_countdown(c: Countdown) -> str:
    """``Fecha hoje`` · ``Fecha amanhã`` · ``Fecha em 4 dias`` · ``Fechada``."""
    if c.kind is CountdownKind.TODAY:
        return "Fecha hoje"
    if c.kind is CountdownKind.TOMORROW:
        return "Fecha amanhã"
    if c.kind is CountdownKind.IN_DAYS:
        return f"Fecha em {_days(c.days)}"
    return "Fechada"


def due_countdown(c: Countdown) -> str:
    """``Vence hoje`` · ``Vence amanhã`` · ``Vence em 11 dias`` · ``Venceu há 3 dias``."""
    if c.kind is CountdownKind.TODAY:
        return "Vence hoje"
    if c.kind is CountdownKind.TOMORROW:
        return "Vence amanhã"
    if c.kind is CountdownKind.IN_DAYS:
        return f"Vence em {_days(c.days)}"
    return f"Venceu há {_days(c.days)}"


def explain_assignment(a: StatementAssignment) -> str:
    """The reason a purchase went to a statement, in Portuguese (CLAUDE.md rule 5)."""
    where = (
        f"fatura de {format_month(a.month)} "
        f"(fecha {format_date_short(a.closing_date)} · vence {format_date_short(a.due_date)})"
    )
    if a.reason is AssignmentReason.EXPLICIT or a.purchase_date is None:
        return f"Fatura escolhida por você: {where}."
    when = format_date(a.purchase_date)
    own_closing = format_date_short(a.own_closing_date or a.closing_date)
    if a.reason is AssignmentReason.BEFORE_CLOSING:
        return f"Compra em {when}, antes do fechamento em {own_closing} → {where}."
    if a.reason is AssignmentReason.ON_CLOSING_DAY:
        return (
            f"Compra em {when}, no dia do fechamento ({own_closing}), "
            f"já vai para a próxima → {where}."
        )
    return f"Compra em {when}, depois do fechamento em {own_closing} → {where}."


def best_day_hint(a: StatementAssignment) -> str:
    best = format_date(a.best_purchase_date)
    return (
        f"Melhor dia de compra neste cartão: {best} "
        "(no dia do fechamento a compra já vai para a próxima fatura)."
    )


def format_rate(mode: RateMode | None, indexer: Indexer | None, bps: int | None) -> str:
    """``110% do CDI`` · ``IPCA + 6,50%`` · ``12,30% a.a.``; ``—`` when there is no rate."""
    if mode is None or bps is None:
        return "—"
    name = INDEXER_LABELS[indexer] if indexer else "índice"
    if mode is RateMode.PERCENT_OF_INDEX:
        return f"{format_bps(bps, keep_decimals=bps % 100 != 0)} do {name}"
    if mode is RateMode.SPREAD_OVER_INDEX:
        return f"{name} + {format_bps(bps)}"
    return f"{format_bps(bps)} a.a."


def describe_alert(alert: RecurringAlert, label: str, last_closed: YearMonth) -> str:
    """The sentence of a recurring alert (last closed month against the one before it)."""
    from financas.domain.money import format_brl

    now, before = format_month(last_closed), format_month(last_closed.add_months(-1))
    if alert.kind is AlertKind.DISAPPEARED:
        was = format_brl(alert.previous_cents or 0)
        return f"{label}: cobrado em {before} ({was}) e não em {now}."
    if alert.kind is AlertKind.CHANGED:
        return (
            f"{label}: de {format_brl(alert.previous_cents or 0)} em {before} para "
            f"{format_brl(alert.current_cents or 0)} em {now}."
        )
    return f"{label}: novo em {now} ({format_brl(alert.current_cents or 0)})."
