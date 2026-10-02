"""pt-BR wording of the spreadsheet import (CLAUDE.md 13.1): flags, issues and report titles."""

from financas.application.imports.model import Flag

FLAG_LABELS = {
    Flag.KIND_CATEGORY_MISMATCH: "tipo e categoria não combinavam (o tipo prevaleceu)",
    Flag.STATEMENT_DISAGREES: "a fatura da planilha difere da regra de fechamento",
    Flag.PROVISIONAL: "Pix de terceiro, lançado como provisório",
    Flag.UNMATCHED_PAYMENT: "pagamento de fatura de fora do período (só a saída)",
    Flag.UNMATCHED_OWN_TRANSFER: "transferência própria sem a outra ponta (uma perna só)",
    Flag.PLAN_GAP: "parcela faltando entre duas existentes",
    Flag.PLAN_OVERLAP: "outro parcelamento igual cobre as mesmas faturas (risco de dobrar)",
    Flag.PAYMENT_ABOVE_OUTSTANDING: "pagamento maior que o saldo da fatura",
    Flag.NO_CARD_FOR_PAYMENT: "pagamento sem cartão identificado (só a saída)",
}

ISSUE_LABELS = {
    "UNKNOWN_ACCOUNT": "Instituição/origem sem conta no arquivo de contas",
    "UNKNOWN_CATEGORY": "Categoria sem equivalente (preencha categorias.csv)",
    "NO_SWEEP_ACCOUNT": "Há movimentos do Rende Fácil, mas falta a conta de investimento",
    "BAD_DECISION": "Decisão de contraparte incompatível com o sinal do lançamento",
    "NO_STATEMENT": "Lançamento de cartão sem fatura na planilha (usou a data)",
    "NO_CARD_FOR_PAYMENT": "Pagamento de fatura sem cartão do mesmo banco",
    "PAYMENT_OUT_OF_SCOPE": "Pagamento de fatura anterior ao período (só o cartão: ignorado)",
    "CARD_SETTINGS_MISSING": "Cartão sem vencimento ou dias de fechamento no arquivo de contas",
    "ACCOUNT_FILE_INVALID": "Linha inválida no arquivo de contas",
}

REPORT = {
    "title": "Importação da planilha — plano (nada foi gravado no banco)",
    "scope": "Linhas no período ({year}, por competência): {scope} · fora do período: {skipped}",
    "actions": "Lançamentos simples: {entries} · transferências: {transfers} · "
    "parcelamentos: {plans} ({present} parcelas na planilha + {generated} geradas) · "
    "pagamentos de fatura: {payments}",
    "attention": "Pontos de atenção (revise em lancamentos.csv):",
    "issues": "Problemas:",
    "none": "nenhum",
    "accounts": "Totais por conta (soma dos lançamentos planejados):",
    "counterparties": "Contrapartes distintas: {total} · próprias: {own} · de terceiros: {third}",
    "next": "Próximo passo: revise contrapartes.csv, categorias.csv, contas.csv e lancamentos.csv "
    "em {directory}; depois rode o plano de novo.",
}

CHECK_LABELS = {
    "COUNT_BY_ACCOUNT": "Quantidade de lançamentos por conta",
    "SUM_BY_ACCOUNT": "Soma dos lançamentos por conta",
    "TRANSFER_LEGS": "Pernas das transferências (no máximo duas, valores opostos)",
    "CARD_ENTRY_STATEMENT": "Lançamentos de cartão ligados a uma fatura",
    "HAS_ENTRIES": "Há lançamentos no banco",
    "OPENING_BALANCE": "Saldo inicial informado",
}
