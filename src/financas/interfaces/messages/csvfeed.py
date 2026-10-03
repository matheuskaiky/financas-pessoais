"""pt-BR wording of the CSV feed (CLAUDE.md 13.3): error codes, warnings, checks, report text.

``CSV_ERROR_MESSAGES`` holds the codes that only the feed has; ``messages/__init__`` merges it into
the catalogue of domain codes. ``FEED_OVERRIDES`` words a shared code better for the file context.
"""

CSV_ERROR_MESSAGES: dict[str, str] = {
    "FILE_NOT_UTF8": "O arquivo não está em UTF-8. Salve como “CSV UTF-8”.",
    "FILE_EMPTY": "O arquivo está vazio: falta a linha de cabeçalho.",
    "DELIMITER_AMBIGUOUS": (
        "Não deu para descobrir o separador do cabeçalho. Use --delimiter ou confira a linha."
    ),
    "UNTERMINATED_QUOTE": "Aspas abertas e não fechadas.",
    "MALFORMED_ROW": "Linha mal formada (aspas ou texto depois das aspas).",
    "TOO_MANY_ROWS": "Arquivo com linhas demais (máximo de {max} linhas de dados).",
    "UNKNOWN_COLUMN": "Coluna desconhecida: “{name}”. Confira o nome (use --template).",
    "DUPLICATE_COLUMN": "A coluna aparece mais de uma vez no cabeçalho.",
    "MISSING_COLUMN": "Falta a coluna obrigatória no cabeçalho.",
    "UNNAMED_COLUMN": "Há um valor numa coluna sem nome no cabeçalho.",
    "ROW_WIDTH": "A linha tem {found} campos, mas o cabeçalho tem {expected}.",
    "REQUIRED_FIELD": "Campo obrigatório vazio.",
    "FIELD_TOO_LONG": "Texto longo demais (máximo de {max} caracteres).",
    "DATE_TOO_FAR": "Data mais de 366 dias no futuro.",
    "DATE_TOO_OLD": "Data anterior a 1900.",
    "AMOUNT_AMBIGUOUS": (
        "Valor ambíguo: “1.234” pode ser mil duzentos e trinta e quatro ou um e vinte e três "
        "centavos. Escreva 1234,00 ou 1.234,00."
    ),
    "AMOUNT_TOO_MANY_DECIMALS": "Valor com mais de duas casas decimais (nunca arredondamos).",
    "INVALID_BOOLEAN": "Valor inválido. Use sim/não, yes/no, true/false, 1/0, s/n ou x.",
    "NOT_ALLOWED_FOR_KIND": "Esta coluna não se aplica ao tipo ({kind_label}); deixe em branco.",
    "REFUND_NOT_INSTALLMENT": "Estorno não pode ser parcelado.",
    "INSTALLMENT_DETAILS_WITHOUT_COUNT": (
        "Parcela atual e tipo de valor só valem com parcelas (2 ou mais)."
    ),
    "INSTALLMENT_NOT_RECURRING": "Uma compra parcelada não é recorrente.",
    "UNKNOWN_ACCOUNT": "Conta não encontrada. Parecidas: {suggestions}.",
    "AMBIGUOUS_ACCOUNT": "Mais de uma conta tem esse apelido.",
    "UNKNOWN_CATEGORY": "Categoria não encontrada. Parecidas: {suggestions}.",
    "AMBIGUOUS_CATEGORY": "Mais de uma categoria corresponde a esse nome.",
    "TRANSFER_CATEGORY_FIXED": "Transferências sempre usam a categoria “Transferência”.",
    "INSTALLMENTS_ONLY_ON_CARDS": "Parcelamento só existe em cartão de crédito.",
    "STATEMENT_NOT_FOUND": (
        "A fatura {statement} não existe: ela precisa já estar cadastrada ou receber compras "
        "deste mesmo arquivo."
    ),
    "PAYMENT_NOTES_NOT_SUPPORTED": "Pagamento de fatura não guarda observações; deixe em branco.",
    "DUPLICATE_BALANCE": "Já há um saldo desta conta nesta data na linha {first_line}.",
    "FEED_PLAN_MISMATCH": "O resultado da compra parcelada (linha {line}) difere do plano.",
}

# the shared code, worded for a CSV file
FEED_OVERRIDES: dict[str, str] = {
    "STATEMENT_REQUIRED": (
        "Informe a fatura (AAAA-MM): obrigatória no pagamento de fatura e na compra em andamento."
    ),
    "TRANSFER_NEEDS_ACCOUNT": "Preencha conta, conta_destino ou as duas (não as duas em branco).",
    "HOLDING_REQUIRED": (
        "Conta de investimento controlada por aplicação: o arquivo não lança nela."
    ),
    "ACCOUNT_TRACKS_HOLDINGS": (
        "Conta de investimento controlada por aplicação: o arquivo não informa saldo nela."
    ),
    "INVALID_CHOICE": "Valor não reconhecido. Veja os valores aceitos em docs/CSV_IMPORT.md.",
    "INVALID_DATE": "Data inválida. Use AAAA-MM-DD ou dd/mm/aaaa (ano com 4 dígitos).",
    "INVALID_YEAR_MONTH": "Fatura inválida. Use AAAA-MM (mês do fechamento).",
    "INVALID_NUMBER": "Número inválido: use só dígitos, sem sinal nem vírgula.",
    "STATEMENT_ALREADY_PAID": (
        "A fatura já está paga: ela é histórico e não recebe novos lançamentos."
    ),
    "STATEMENT_ONLY_FOR_CARDS": "A coluna fatura só vale para cartão de crédito.",
}

WARNING_MESSAGES: dict[str, str] = {
    "DUPLICATE_ROWS": (
        "{count} linha(s) idêntica(s) a outra anterior. Lançamentos iguais no mesmo dia são "
        "legítimos e não são removidos: confira se não colou a mesma linha duas vezes."
    ),
    "STATEMENT_OVERRIDES_CYCLE": (
        "a fatura {statement} foi escolhida por você; pela data a regra do ciclo daria {cycle}."
    ),
    "PAYMENT_ABOVE_OUTSTANDING": (
        "pagamentos da fatura {statement} do cartão {account} deixam a fatura com crédito "
        "(pago acima do devido)."
    ),
    "BALANCE_REPLACES_EXISTING": "já havia um saldo nesta conta e data; será substituído.",
}

CHECK_LABELS: dict[str, str] = {
    "COUNT_BY_ACCOUNT": "Quantidade de lançamentos criados por conta",
    "SUM_BY_ACCOUNT": "Soma dos lançamentos criados por conta",
    "STATEMENT_TOTALS": "Quantidade e soma por fatura",
    "TRANSFER_LEGS": "Pernas das transferências (no máximo duas, valores opostos)",
    "CARD_ENTRY_STATEMENT": "Lançamentos de cartão ligados a uma fatura",
    "BALANCE_RECORDED": "Saldos informados gravados",
}

REASON_LABELS = {
    "before_closing": "antes do fechamento",
    "on_closing_day": "no dia do fechamento (vai para a próxima)",
    "after_closing": "depois do fechamento",
    "explicit": "fatura escolhida por você",
}

# stored as data on a payment row that has no description (the use case never writes text)
PAYMENT_DESCRIPTION = "Pagamento de fatura"

KIND_PLURAL = {
    "expense": "despesas",
    "income": "receitas",
    "refund": "estornos",
    "transfer": "transferências",
    "balance": "saldos",
}

REPORT = {
    "title_dry": "Importação por CSV — plano (nada foi gravado no banco)",
    "title_apply": "Importação por CSV — aplicando",
    "file": "Arquivo: {rows} linha(s) de dados · separador {delimiter} · SHA-256 {sha}",
    "rows": "Linhas por tipo: {kinds}",
    "created": (
        "Serão criados: {transactions} lançamento(s) (parcelas e pernas de transferência "
        "incluídas) · compras parceladas: {purchases} · pagamentos de fatura: {payments} · "
        "saldos informados: {balances}"
    ),
    "accounts": "Totais por conta (soma dos lançamentos que serão criados):",
    "statements": "Faturas tocadas:",
    "statement_line": (
        "  {account} · fatura {month} (fecha {closing} · vence {due}, {state}): "
        "{entries} compra(s)/estorno(s) · {payments} pagamento(s) · "
        "compras líquidas {owed} · pagos {paid}"
    ),
    "state_new": "nova",
    "state_existing": "já existe",
    "reasons": "Fatura de cada lançamento de cartão: {reasons}",
    "warnings": "Avisos:",
    "none": "nenhum",
    "next": "Nada foi gravado. Para gravar: acrescente --apply (um backup é feito antes).",
    "errors_title": "O arquivo tem {count} problema(s); nada foi gravado.",
    "errors_more": "… e mais {count} problema(s) não listado(s) (mostrando os {shown} primeiros).",
    "issue": "  linha {line} · coluna {column}: [{code}] {message}",
    "issue_nocolumn": "  linha {line}: [{code}] {message}",
    "issue_file": "  arquivo: [{code}] {message}",
    "warning_line": "  linha {line}: {message}",
    "warning_plain": "  {message}",
    "no_rows": "O arquivo não tem linhas de dados (só o cabeçalho).",
    "already": (
        "Este mesmo arquivo (SHA-256 {sha}) já foi aplicado em {when}. "
        "Nada foi feito. Para aplicar de novo mesmo assim, use --force."
    ),
    "backup": "Backup criado em {path}.",
    "check_ok": "  ok · {label}",
    "check_fail": "  FALHOU · {label} ({detail})",
    "verify_failed": "As conferências falharam: o banco não foi alterado.",
    "apply_failed": "A aplicação falhou ({code}): o banco não foi alterado.",
    "done": (
        "Concluído: {entries} lançamento(s) simples, {purchases} compra(s) parcelada(s), "
        "{transfers} transferência(s), {payments} pagamento(s) de fatura, {balances} saldo(s). "
        "Revise pelo painel (`financas serve`)."
    ),
    "balance_diff": "  Saldo {account} em {date}: informado {informed} · calculado {computed} · "
    "diferença {difference}",
    "balance_diff_none": "  Saldo {account} em {date}: informado {informed} (sem saldo anterior "
    "para comparar)",
    "balances_title": "Saldos informados × calculado pelo sistema:",
    "no_database": "Banco de dados não encontrado em {path}. Crie-o antes (`financas init`).",
    "needs_migration": (
        "O banco está desatualizado em relação ao código. Atualize-o antes "
        "(`uv run alembic upgrade head`) ou use --apply, que migra a cópia de trabalho."
    ),
    "unreadable": "Não foi possível ler o arquivo: {reason}",
}
