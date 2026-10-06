"""pt-BR wording of the web import page (``/importar``, CLAUDE.md 13.3).

Plain, non-technical text for the person who prepared the file. Error codes come from
``application/csvfeed``; the CLI wording (``messages/csvfeed.py``) mentions flags that make no
sense on a web page, so those codes are reworded here.
"""

IMPORT_ISSUE_OVERRIDES: dict[str, str] = {
    "DELIMITER_AMBIGUOUS": (
        "Não deu para ler o cabeçalho do arquivo. Confira a primeira linha: os nomes das "
        "colunas devem vir separados por ponto e vírgula, vírgula ou tabulação."
    ),
    "UNKNOWN_COLUMN": (
        "Coluna desconhecida: “{name}”. Confira o nome no cabeçalho ou baixe o arquivo de exemplo."
    ),
    "INVALID_CHOICE": "Valor não reconhecido. Veja os valores aceitos no guia do formato.",
}

IMPORT = {
    "page_title": "Importar lançamentos",
    "subtitle": "Envie um arquivo com suas movimentações para registrar no sistema com segurança.",
    "issue_line_column": "Linha {line}, coluna “{column}”: {message}",
    "issue_line": "Linha {line}: {message}",
    "issue_file": "{message}",
    "no_file": "Escolha um arquivo para continuar.",
    "wrong_type": "Este não parece ser o arquivo certo: ele precisa terminar em .csv.",
    "too_large": "O arquivo é grande demais (o limite é de {limit} MB). Divida em partes menores.",
    "unreadable": "Não foi possível ler o arquivo enviado. Tente escolher de novo.",
    "no_rows": "O arquivo não tem lançamentos: só o cabeçalho. Preencha ao menos uma linha.",
    "needs_migration": (
        "O banco de dados precisa ser atualizado antes de importar. Reinicie o sistema."
    ),
    "no_database": "O banco de dados não foi encontrado.",
    "already": (
        "Este mesmo arquivo já foi importado em {when}. Para não duplicar lançamentos, "
        "nada foi feito."
    ),
    "expired": "A análise expirou ou o arquivo não está mais disponível. Envie o arquivo de novo.",
    "busy": "Já existe uma importação em andamento. Aguarde alguns segundos e tente de novo.",
    "demo": "Modo demonstração: a importação não grava nada.",
    "apply_failed": "A importação não foi concluída ({reason}). Nada foi alterado no sistema.",
    "apply_failed_code": (
        "Algo deu errado ao importar (código {code}). Nada foi alterado no sistema."
    ),
    "verify_failed": (
        "As conferências automáticas não bateram com o esperado. "
        "Nada foi alterado no sistema; seus dados continuam como estavam."
    ),
    "problems_title": "O arquivo tem {count} problema(s)",
    "problems_help": "Corrija o arquivo e envie de novo. Nada foi gravado.",
    "problems_more": "… e mais {count} problema(s) que não foram listados.",
    "ok_title": "Arquivo conferido",
    "ok_text": "Nada foi gravado ainda. Confira os números e, estando de acordo, aplique.",
    "created": "{count} lançamento(s) serão registrados (parcelas e transferências incluídas).",
    "applied_title": "Importação concluída",
    "applied_text": "{count} lançamento(s) registrados. Um backup foi feito antes de gravar.",
    "balance_line": "Saldo informado de {account} em {date}",
    "balance_unknown": "sem saldo anterior para comparar",
}

COUNT_LABELS = {
    "income": "Receitas",
    "expense": "Despesas",
    "transfer": "Transferências",
    "installments": "Parcelamentos",
    "payments": "Pagamentos de fatura",
    "refund": "Estornos",
    "balance": "Saldos informados",
}
