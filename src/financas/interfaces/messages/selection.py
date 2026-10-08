"""pt-BR wording of Selection Mode ("Modo de Seleção"): lock reasons, merge reasons, dialog text.

The application returns codes (``SelectionLock``, ``MergeBlock``); every sentence lives here,
character for character as in the design handoff (checklist, Appendix A).
"""

from financas.application.queries.selection import RowSelection
from financas.domain.services.selection import MAX_SELECTION, MergeBlock, SelectionLock

# One duration for every transient message (owner decision: 5 s, paused while hovered).
MESSAGE_SECONDS = 5

LOCK_REASONS: dict[SelectionLock, str] = {
    SelectionLock.PAST_MONTH: (
        "Apenas compras do mês atual podem ser mescladas. "
        "Lançamentos de meses anteriores já estão consolidados."
    ),
    SelectionLock.FUTURE_MONTH: "Apenas compras do mês atual podem ser mescladas.",
    SelectionLock.STATEMENT_PAID: "Fatura paga: os lançamentos ficam no histórico.",
    SelectionLock.PLAN_ALL_PAID: "Todas as parcelas já foram pagas.",
}

SELECT_AT_LEAST_ONE = "Selecione ao menos 1 lançamento para apagar"
SELECT_AT_LEAST_TWO = "Selecione ao menos 2 lançamentos da mesma conta e fatura"
PICK_ONE_MORE = "Escolha mais um lançamento da mesma conta e fatura"
MISMATCH = "Selecione itens da mesma conta e fatura para mesclar"
CAP_REACHED = f"Selecione até {MAX_SELECTION} lançamentos por vez."
MODE_ON = "Modo de seleção ativado. 0 selecionados."
MODE_OFF = "Modo de seleção desativado."


def merge_reason(block: MergeBlock, plan_installments: int = 0) -> str:
    if block is MergeBlock.PLAN:
        return f"Compra parcelada em {plan_installments} faturas não pode ser mesclada"
    if block is MergeBlock.REFUNDED:
        return "Compra estornada não pode ser mesclada"
    if block is MergeBlock.ITEMIZED:
        return "Lançamento com itens não pode ser mesclado"
    return "Só despesas comuns podem ser mescladas"


def lock_reason(selection: RowSelection) -> str:
    assert selection.lock is not None
    return LOCK_REASONS[selection.lock]


def row_reason(selection: RowSelection) -> str:
    """The text of ``data-sel-reason``: why the row is locked (empty when it is not)."""
    return lock_reason(selection) if selection.lock else ""


def row_merge_reason(selection: RowSelection) -> str:
    if selection.merge_block is None:
        return ""
    return merge_reason(selection.merge_block, selection.plan_installments)


def plural_rows(count: int) -> str:
    """ "1 lançamento" / "N lançamentos": explicit, because CLDR files 0 under "one"."""
    return f"{count} lançamento" if count == 1 else f"{count} lançamentos"


def client_config() -> dict[str, str | int]:
    """The sentences ``static/selection-mode.js`` shows (the page carries them as a JSON block)."""
    return {
        "seconds": MESSAGE_SECONDS,
        "max": MAX_SELECTION,
        "need_delete": SELECT_AT_LEAST_ONE,
        "need_merge": SELECT_AT_LEAST_TWO,
        "pick_more": PICK_ONE_MORE,
        "mismatch": MISMATCH,
        "cap": CAP_REACHED,
        "mode_on": MODE_ON,
        "mode_off": MODE_OFF,
        "selected_one": "selecionado",
        "selected_many": "selecionados",
        "deleted_one": "1 lançamento apagado.",
        "deleted_many": "{n} lançamentos apagados.",
        "delete_error": "Não foi possível apagar os lançamentos. Nada foi alterado.",
        "merge_outside": LOCK_REASONS[SelectionLock.PAST_MONTH],
    }
