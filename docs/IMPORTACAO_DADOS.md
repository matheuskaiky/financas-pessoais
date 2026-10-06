# Importação de dados: formato do arquivo

## Resumo (pt-BR)

Para registrar muitas movimentações de uma vez, prepare um arquivo de texto separado por vírgula (`.csv`) no formato descrito abaixo e envie-o pela página **Importar dados** (menu lateral, ou **Mais** no celular; endereço `/importar`):

1. **Envie** o arquivo. O sistema confere tudo **sem gravar nada** e mostra quantas receitas, despesas, transferências, parcelamentos e pagamentos de fatura serão registrados. Se houver erro, a página aponta a linha e a coluna, e o botão de aplicar não aparece.
2. **Aplique.** O sistema faz um backup, grava tudo ou nada, e mostra quantos lançamentos entraram. O mesmo arquivo não é aplicado duas vezes.
3. As contas, os cartões e as categorias citados no arquivo precisam existir antes. Na página, "Baixar um arquivo de exemplo" entrega um modelo com uma linha de cada tipo.

No modo demonstração a página confere o arquivo, mas não grava. Pelo terminal o mesmo fluxo existe em `scripts/feed_from_csv.py` (simulação por padrão, `--apply` para gravar). A especificação detalhada, em inglês, segue abaixo.

# CSV feed: loading entries from a file you prepare

This guide is the **specification** of `scripts/feed_from_csv.py`. If a rule changes in the code,
change it here too (CLAUDE.md section 13.3). Everything below was run against a scratch database;
the outputs are real (the screens of the tool are in Portuguese, as everything the user sees).

## 1. Purpose and scope

The feed loads **entries typed by you** into the system in one go: income, expenses, refunds,
transfers (including contributions to investments and credit-card payments), card purchases
(single, in installments, already running), and informed balances. You write the file in the format
described here, from a spreadsheet or a text editor, and the script puts it in the database through
the same use cases the web screens and the CLI use. Every rule of the system therefore holds by
construction: signs, categories by kind, transfers that are neither income nor expense, card
statements and the closing rule, installments, and so on.

What it is **not**:

- It is **not a bank-statement importer**. It reads no OFX, no bank CSV, no PDF and guesses nothing
  about someone else's layout. The file is written *for this system*.
- It does **not create** institutions, accounts, cards or categories. They must already exist
  (create them in the app or with `financas account add`, `financas card add`...). An unknown name
  is an error.
- It does **not handle investment holdings** (CDB, LCI... with contract data): only whole-account
  movements and valuations of investment accounts that track by account.
- It does **not edit or delete** anything that already exists. It only adds (a balance on a date that
  already has one replaces it; the report warns first).
- It does **not remove duplicates**: identical rows on the same day are legitimate. It only warns
  with the number of exact duplicate rows, so you can spot a pasted block.

The web page **Importar dados** (`/importar`) runs the same code (`application/csvfeed/service.py`)
with the same rules: dry run first, backup, working copy, all-or-nothing, and the same file (by SHA-256)
refused twice. Uploads are limited to 5 MB. In demo mode the page analyses the file but never applies it.

## 2. Prerequisites

- The database exists and is migrated (`uv run financas init`), with the initial categories.
- Every account, card and category the file mentions exists. Account names are the **nicknames**
  you gave; category names are the **names or slugs** (`Supermercado` or `groceries`).
- Cards have their due day and "closes N days before due" filled in (CLAUDE.md 9.3).
- Run from the project folder, in WSL, with `uv`: `uv run python scripts/feed_from_csv.py ...`
- For the **script**: the server (`financas serve`) must not be writing at the same time (SQLite has one
  writer). The web page does not have this limit: it is the server itself, and applies one import at a time.

## 3. Quick start (5 minutes)

```bash
# 1. a template: the header plus one commented example row per kind
uv run python scripts/feed_from_csv.py --template > october.csv

# 2. fill it in (Excel, Google Sheets or a text editor); keep the header, delete the "# ..." lines
#    you do not need, and save as CSV UTF-8

# 3. dry run (the default): checks everything, prints the plan, writes nothing
uv run python scripts/feed_from_csv.py october.csv

# 4. read the report (section 9 explains it); fix the file if it lists problems, repeat step 3

# 5. apply: backup, working copy, checks, then the swap
uv run python scripts/feed_from_csv.py october.csv --apply
```

The template looks like this (semicolon-delimited, which is what Excel in pt-BR produces):

```text
date;kind;account;to_account;amount;description;category;recurring;notes;statement;installments;installment_number;amount_type;gross_amount
# 2026-10-05;expense;Checking;;-45,90;Pharmacy;Health;no;;;;;;
# 2026-10-05;income;Checking;;3.500,00;Salary October;Salary;yes;;;;;;
# 2026-10-06;refund;Card;;12,50;Store refund;;;;2026-10;;;;
# 2026-10-07;transfer;Checking;Savings;500,00;Monthly contribution;;;;;;;;
# 2026-10-08;expense;Card;;1.200,00;Notebook;Shopping;;;;3;;total;
# 2026-10-08;expense;Card;;100,00;Course (running);Education;;;2026-11;10;3;installment;
# 2026-10-15;transfer;Checking;Card;850,00;Card payment;;;;2026-10;;;;
# 2026-10-31;balance;Savings;;10.250,00;Month end;;;;;;;;10.400,00
```

Lines starting with `#` are ignored, so the examples do nothing until you remove the `# `. The
account names (`Checking`, `Savings`, `Card`) and categories are placeholders: use your own.

Command line:

| Option | Meaning |
|---|---|
| `FILE` | The CSV. |
| (none) | Dry run: validate and print the plan. Writes nothing, not even a backup. |
| `--apply` | Write to the database (section 11). |
| `--force` | With `--apply`: apply even if this exact file was already applied. |
| `--delimiter X` | `,` `;` or `tab`; overrides the detection. |
| `--template` | Print the template and exit. |

Exit codes: `0` ok, `1` usage or read error (no file, no database, empty file), `2` the file has
problems (nothing was written), `3` the apply failed or a check failed (the database was not
changed), `4` this file was already applied.

## 4. File format

### 4.1 Container rules

| Topic | Rule |
|---|---|
| Encoding | UTF-8, with or without BOM. Anything else is `FILE_NOT_UTF8` (the tool never guesses an encoding). |
| Delimiter | Detected from the header line among `,` `;` and tab: the one that makes the most header cells valid column names wins. If two tie, `DELIMITER_AMBIGUOUS`: pass `--delimiter`. |
| Quoting | RFC 4180: a field with the delimiter, a quote or a line break goes in `"..."`; a quote inside is doubled `""`. |
| Header | The first non-empty, non-comment line. Names ignore case, accents, spaces and hyphens (`Conta Destino`, `conta-destino` and `conta_destino` are the same). Unknown names are an error (a typo must not pass silently); a name twice is an error; an empty header cell is allowed only if every cell under it is empty. |
| Ignored lines | Empty lines, lines made only of delimiters (what Excel exports for empty rows) and lines whose first character is `#`. |
| Fields per row | Exactly as many as the header (extra *empty* trailing cells are tolerated). Otherwise `ROW_WIDTH`. |
| Size | At most 20,000 data rows (`TOO_MANY_ROWS`). |
| Text values | NFC-normalised and trimmed. Descriptions are otherwise kept intact: no case change, no whitespace rewriting. Max 300 characters (notes 2,000). |
| Line numbers in errors | Physical lines of the file, counting the header, comments and blank lines, so they match what an editor shows. |

### 4.2 Columns

Column names are in English; the Portuguese aliases are accepted in the header.

| Column | Aliases | Type and accepted values | Required for |
|---|---|---|---|
| `date` | `data` | `YYYY-MM-DD` or `dd/mm/yyyy` (`d/m/yyyy` too); 4-digit year mandatory; not after today + 366 days; not before 1900. | all kinds |
| `kind` | `tipo` | `expense` (`despesa`), `income` (`receita`), `refund` (`estorno`), `transfer` (`transferencia`, `transferência`), `balance` (`saldo`). | all |
| `account` | `conta` | Nickname of an existing account (case/accent-insensitive). For `transfer` it is the **origin** and may be blank. | all (blank allowed only on `transfer`) |
| `to_account` | `conta_destino` | Nickname; the **destination** of a transfer. Only for `transfer`. | `transfer` (one of `account`/`to_account` may be blank, not both) |
| `amount` | `valor` | Money, section 5.1. | all |
| `description` | `descricao` | Free text. | `expense`, `income`, `refund`; optional for `transfer` and `balance` |
| `category` | `categoria` | Name or slug of an existing category. Blank means the default of the kind. | never |
| `recurring` | `recorrente` | Boolean, section 5.3. Blank = no. Only meaningful (and only accepted as true) on `expense`, `income`, `refund`; never with installments. | never |
| `notes` | `observacoes` | Free text. On a `balance` it is stored in the balance note (after the description). Not accepted on a statement payment. | never |
| `statement` | `fatura` | `YYYY-MM`, the **closing month** of a card statement. | payments; running installments; optional on card entries |
| `installments` | `parcelas` | Integer 1 to 120: the total number of installments. Only `expense` on a card (2 or more). | never |
| `installment_number` | `parcela_atual` | Integer, the current installment (default 1). Only with `installments`. | never |
| `amount_type` | `tipo_valor` | `total` (default) or `installment` (`parcela`). Only with `installments` of 2 or more. | never |
| `gross_amount` | `valor_bruto` | Money. Only on a `balance` of an **investment** account. | never |

The columns `date`, `kind`, `account` and `amount` must exist in the header. If the file has any
`transfer` row, give the `to_account` column too (an absent column would silently mean "to an
account the system does not track").

Which columns a kind accepts (anything else must be blank, `NOT_ALLOWED_FOR_KIND`):

| Kind | Accepts |
|---|---|
| `expense` | date, account, amount, description, category, recurring, notes, statement (cards), installments, installment_number, amount_type (cards) |
| `income` | date, account, amount, description, category, recurring, notes |
| `refund` | date, account, amount, description, category, recurring, notes, statement (cards) |
| `transfer` | date, account, to_account, amount, description, category (only `Transferência`), notes, statement (payments) |
| `balance` | date, account, amount, description, notes, gross_amount |

## 5. Normalisation and parsing rules

### 5.1 Amounts

Parsed with `Decimal`, never `float`. The result is whole cents.

- Accepted: `1234.56`, `1234,56`, `1.234,56`, `1,234.56`, `R$ 1.234,56`, `R$1.234,56`, `1234`,
  `0,5` (= 0,50), `1.234,5` (= 1.234,50), with an optional sign `+`/`-`/`−` before the number or
  before/after `R$` (`-R$ 5,00`, `R$ -5,00`). A non-breaking space (from spreadsheets) is fine.
- **The rule:** the *last* `.` or `,` is the decimal separator **when it is followed by exactly 1 or
  2 digits**. Groups of exactly 3 digits are thousands separators. So `1.234,56` and `1,234.56`
  are 1,234.56 and `1.234.567` is 1,234,567.00 (a separator repeated can only be thousands).
- **Ambiguous, rejected** (`AMOUNT_AMBIGUOUS`): one separator followed by exactly 3 digits, like
  `1.234` or `1,234`. It could be one thousand two hundred thirty-four or one and 234 thousandths;
  the tool never guesses. Write `1234,00` or `1.234,00`.
- More than 2 decimals is `AMOUNT_TOO_MANY_DECIMALS` (never rounded): `1.234,567`, `12.3456`,
  `1234.567`.
- Anything else is `INVALID_AMOUNT`: letters, spaces inside the number (`12 34`), `1e3`, `(5,00)`,
  `5%`, a leading or trailing separator (`,50`, `5,`), mixed groupings (`1,23.45`), non-ASCII digits.
- Zero is refused (`AMOUNT_NOT_POSITIVE`) except in a balance. Magnitude must be below
  10,000,000,000.00 (`AMOUNT_TOO_LARGE`).

| You write | Result |
|---|---|
| `1234.56` / `1234,56` / `1.234,56` / `1,234.56` | 1,234.56 |
| `R$ 1.234,56` / `-R$ 1.234,56` | 1,234.56 / minus 1,234.56 |
| `12.5` | 12.50 |
| `1.234.567` | 1,234,567.00 |
| `1.234` / `1,234` | **error: ambiguous** |
| `1.234,567` | **error: more than 2 decimals** |
| `0` | error (except balance) |

### 5.2 Signs

The kind decides the meaning; the sign in the file may only agree with it.

| Kind | Allowed in the file | Stored as |
|---|---|---|
| `expense` | unsigned or negative (`45,90`, `-45,90`) | negative |
| `income` | unsigned or positive | positive |
| `refund` | unsigned or positive | positive (it reduces spending) |
| `transfer` | **unsigned only**; direction comes from `account` → `to_account` | origin negative, destination positive |
| `balance` | signed (an overdrawn checking account is negative) | as written |

A sign that contradicts the kind (`expense` with `+10`, `income` with `-10`, any sign on a
transfer) is `SIGN_KIND_MISMATCH`.

### 5.3 Booleans

`yes`, `true`, `1`, `sim`, `s`, `x` mean true; `no`, `false`, `0`, `não`, `nao`, `n` and a blank
cell mean false (case- and accent-insensitive). Anything else is `INVALID_BOOLEAN`.

### 5.4 Dates

`2026-09-30` or `30/09/2026` (also `5/9/2026`). Two-digit years (`30/09/26`), other separators,
impossible dates (`31/02/2026`, `29/02/2027`) are `INVALID_DATE`. More than 366 days after today is
`DATE_TOO_FAR` (a typo like `2062` instead of `2026`); before 1900 is `DATE_TOO_OLD`. The date of a
card purchase is the purchase date, which decides the statement (section 7.4).

### 5.5 Names, accents and case

Accounts are matched by their nickname, categories by name **or** slug, with the same key the
system uses for searching: case-folded, accents removed, inner spaces collapsed. So `cartao exemplo`,
`CARTÃO  EXEMPLO` and `Cartão Exemplo` are the same card; `educacao` finds `Educação`.

If two accounts (or two categories) have the same key, the row is an error (`AMBIGUOUS_ACCOUNT`,
`AMBIGUOUS_CATEGORY`): ambiguity is never guessed. An unknown name is an error that lists the closest
known names (`Parecidas: ...`).

### 5.6 Delimiters, BOM, quoting

Excel in pt-BR saves `;`-delimited with decimal commas; Google Sheets exports `,`-delimited, and
amounts with a decimal comma then arrive quoted (`"1.234,56"`). Both work. A BOM at the start is
dropped. A field with the delimiter must be quoted: `"Mercado, padaria e feira"`. An open quote that
is never closed is `UNTERMINATED_QUOTE`; text after a closing quote is `MALFORMED_ROW`. A line that
starts with `#` *inside* a quoted multi-line field is data, not a comment.

## 6. How rows are checked: two passes

1. **Syntax** (no database): encoding, header, every cell's format, required cells per kind, columns
   not allowed for the kind. All problems of all rows are collected (up to 200 are printed), each
   with its **line** and **column**, and the run stops with exit code 2.
2. **Business rules** (reads the database): names exist, kinds of accounts and categories agree,
   statements, installments, payments, balances. Again all problems are collected and nothing is
   written.

So a file with typos may show you a second list of problems after you fix the first. Only a file
that passes both passes is applied, and only as a whole.

## 7. Business rules and why

### 7.1 What each account kind accepts

| Account kind | `expense` | `income` | `refund` | `transfer` | `balance` |
|---|---|---|---|---|---|
| Checking | yes | yes | yes | yes (origin or destination) | yes |
| Credit card | yes (purchases, charges) | no | yes | only as **destination**: a statement payment | no |
| Investment | no | no | no | yes (contribution or withdrawal) | yes |

Inactive accounts are refused. Investment accounts that track by holding (`tracking = holdings`) are
refused for transfers and balances: their movements belong to a holding, which this feed does not
handle.

### 7.2 Categories and kinds

The category's kind must match the entry's kind: an expense takes an expense category, income an
income category, a refund a neutral one, a transfer only the neutral `Transferência`. A mismatch is
`CATEGORY_KIND_MISMATCH` (the old spreadsheet allowed this and the reports lied). A blank category
becomes `uncategorized` (expense), `other_income` (income), `refund` (refund) or `transfer`.
Transfers always use the category `transfer`; any other category on a transfer is an error.

### 7.3 Transfers are neither income nor expense

A transfer moves your own money. It has one or two legs (same transfer id, opposite amounts, different
accounts), created together:

- `account` = origin (money leaves), `to_account` = destination (money arrives).
- Between two accounts the system tracks: two legs.
- One of them blank: **one leg** only (the other account is not tracked by the system, such as a
  friend's account or a brokerage you do not follow). Both blank is `TRANSFER_NEEDS_ACCOUNT`; the same
  account twice is `TRANSFER_SAME_ACCOUNT`.
- Contribution to an investment: checking → investment. Withdrawal: investment → checking.
- They never count as income or expense; the balance of a checking account includes them.

### 7.4 Card purchases and the closing rule (CLAUDE.md 9.3)

A card entry belongs to a **statement**, identified by its closing month. The card closes `N` days
before its due date, and **a purchase made on the closing date already goes to the next statement**.
A purchase before the closing date stays in the statement of its own month. The system uses the
**stored dates** of statements that already exist, and the card's settings only for months with no
statement yet (dates are frozen once created).

Worked table, card due on day 5, closing 11 days before the due date:

| Purchase date | Statement | Closes | Due |
|---|---|---|---|
| 2026-07-20 | 2026-07 | 2026-07-25 | 2026-08-05 |
| 2026-07-24 | 2026-07 | 2026-07-25 | 2026-08-05 |
| 2026-07-25 | 2026-08 | 2026-08-25 | 2026-09-05 |
| 2026-09-23 | 2026-09 | 2026-09-24 | 2026-10-05 |
| 2026-09-24 | 2026-10 | 2026-10-25 | 2026-11-05 |
| 2026-12-25 | 2027-01 | 2027-01-25 | 2027-02-05 |
| 2027-02-21 | 2027-02 | 2027-02-22 | 2027-03-05 |
| 2027-02-22 | 2027-03 | 2027-03-25 | 2027-04-05 |

(September has 30 days, so this card closes on the 24th; in February on the 22nd: the closing date
follows the due date.)

Leave `statement` blank and the cycle decides. Fill `statement` (`YYYY-MM`) and **your choice wins**,
for the case where the issuer put the purchase elsewhere. The dry run reports how many card entries
fell in each case (before closing, on the closing day, after closing, chosen by you) and warns for
every row whose chosen statement differs from what the cycle would give
(`STATEMENT_OVERRIDES_CYCLE`, with the line). The chosen statement is stored on the entry.

A statement that is **already paid** is history: entries may not be added to it
(`STATEMENT_ALREADY_PAID`), whether by the cycle or by your choice.

**Competence:** a card entry counts in the **statement month**, everything else in the month of its
date (CLAUDE.md open decision 1). The app does this; you do not have to.

### 7.5 Installments (CLAUDE.md 9.4)

`installments` = 2 to 120, only on a card `expense`. One plan and **one entry per installment** are
generated, future ones included, so the amount to pay is exact.

- `amount_type = total` (default): `amount` is the whole purchase. Installments 2..N get
  ⌊total ÷ N⌋ cents and **the first one absorbs the remainder**. R$ 78,23 in 10x = 7,85 + 9 × 7,82.
  R$ 301,00 in 3x bought on 2026-07-26 (card due 5, closes 11 days before):

  | Installment | Amount | Statement | Due |
  |---|---|---|---|
  | 1/3 | 100,34 | 2026-08 | 2026-09-05 |
  | 2/3 | 100,33 | 2026-09 | 2026-10-05 |
  | 3/3 | 100,33 | 2026-10 | 2026-11-05 |

- `amount_type = installment`: `amount` is one installment, as printed on the statement, repeated.
- The statement of installment *k* is the statement of the first generated installment + *k* months.
  Installment 1 is dated on the purchase date; the others on the last day of their statement.
- **A purchase already running:** `installment_number = n` (the one on the statement you are
  looking at) and `statement` = that statement (**mandatory**, `STATEMENT_REQUIRED`). Installments
  `n..N` are generated; earlier ones are not. "Installment 3 of 10 on the 2026-09 statement"
  generates 8 installments, 2026-09 to 2027-04. `date` is then the original purchase date (nothing is
  posted on it). With `amount_type = total` the amounts are those of the split of the *whole*
  purchase.
- A refund can never be an installment (`REFUND_NOT_INSTALLMENT`); an installment purchase is not
  recurring (`INSTALLMENT_NOT_RECURRING`). A total too small to give every installment at least one
  cent is `INSTALLMENT_AMOUNT_TOO_SMALL`.

### 7.6 Paying a statement

A transfer whose **destination is a card** is a payment of one of its statements:

- `account` = a checking account (or blank: paid from an account the system does not track, one leg);
- `to_account` = the card; `statement` = the closing month, **mandatory**; `amount` = what was paid
  (partial and multiple payments are fine); `description` optional (blank gets a standard text);
  `notes` not accepted.
- The statement must exist: either already in the database or created by a purchase/refund/installment
  **in the same file** (payments run after all purchases, whatever the row order). Otherwise
  `STATEMENT_NOT_FOUND`.
- If your payments exceed what the statement owes, the plan warns (`PAYMENT_ABOVE_OUTSTANDING`).
- It stays a transfer: the purchases were already expenses.

### 7.7 Refunds

Positive amount, kind `refund`, neutral category (default `Estorno`). They are shown apart from
expenses (CLAUDE.md open decision 3). On a card, a refund goes to a statement exactly like a purchase
and reduces its total.

### 7.8 Balances and the computed-versus-informed difference

`balance` records the balance you see in the bank on that date (`BalanceAnchor`):

- Checking: signed value; negative when overdrawn.
- Investment: `amount` is the **net redemption value** as the institution shows it (after estimated
  tax); `gross_amount` is optional and may not be below the net.
- Not for cards (`BALANCE_NOT_FOR_CARDS`); gross only for investments (`GROSS_ONLY_FOR_INVESTMENTS`,
  `INVALID_GROSS_BALANCE`); one row per account and date in the file (`DUPLICATE_BALANCE`). A date that
  already has a balance is **replaced** (the plan warns: `BALANCE_REPLACES_EXISTING`).
- Balances are recorded **last**, after all entries, so the system can compare: for each balance the
  apply report shows *informed × computed × difference*. For a checking account a difference points to
  missing entries; for an investment it is the yield. With no earlier balance there is nothing to
  compare ("sem saldo anterior para comparar").

### 7.9 Duplicates

Nothing is deduplicated (CLAUDE.md 13.2: two identical purchases on the same day are normal). The plan
only warns with the **count** of rows identical to an earlier one (`DUPLICATE_ROWS`).

## 8. One worked example per kind

The file `scripts/examples/entries_example.csv` has all of them (synthetic names). It expects checking
accounts `Conta Corrente` and `Conta Reserva`, an investment account `Caixinha Exemplo` and a card
`Cartão Exemplo` (due day 5, closes 11 days before). Its rows and what they do:

| Row (abbreviated) | Effect in the system |
|---|---|
| `2026-09-01;despesa;Conta Corrente;;-1.250,00;Aluguel;Casa;sim` | An expense of R$ 1.250,00 in category Casa, recurring, on 2026-09-01. |
| `05/09/2026;income;Conta Corrente;;5.000,00;Salário setembro;Salário;yes` | Income of R$ 5.000,00, category Salário, recurring. |
| `2026-09-10;expense;Conta Corrente;;R$ 89,90;...;supermercado` | Expense of R$ 89,90; the category is found by slug. |
| `2026-09-12;refund;Conta Corrente;;25,00;Estorno farmácia;` | Refund of R$ 25,00, category `refund` (default). |
| `2026-09-15;transfer;Conta Corrente;Conta Reserva;300,00;...` | Two legs, -300,00 and +300,00; not income, not expense. |
| `2026-09-16;transfer;Conta Corrente;Caixinha Exemplo;1.000,00;Aporte mensal` | A contribution: checking -1.000,00, investment +1.000,00. |
| `2026-08-20;expense;Cartão Exemplo;;150,00;Livraria;Compras` | Card purchase before the closing date (08-25): statement 2026-08. |
| `2026-09-10;expense;Cartão Exemplo;;39,90;Streaming;Assinaturas;sim` | Card, recurring, statement 2026-09 (closes 09-24). |
| `2026-08-26;...;Cartão Exemplo;;301,00;Fone de ouvido;Compras;;compra em 3x;;3;;total` | 3 installments 100,34 / 100,33 / 100,33 on statements 2026-09, 10 and 11 (bought after the 08-25 closing). One plan. |
| `2026-03-10;...;350,00;Curso online;Educação;;...;2026-09;10;4;installment` | A running purchase: installment 4 of 10 is on statement 2026-09; generates installments 4..10 (7 entries of 350,00, statements 2026-09 to 2027-03). |
| `2026-09-12;refund;Cartão Exemplo;;20,00;Estorno loja;` | Card refund: reduces statement 2026-09 by 20,00. |
| `05/09/2026;transfer;Conta Corrente;Cartão Exemplo;150,00;;;;;2026-08` | Payment of the 2026-08 statement from checking: -150,00 on checking, +150,00 on the card, linked to the statement. |
| `2026-09-30;saldo;Conta Corrente;;3.456,78;Conferência com o extrato` | Informed balance of checking on 09-30. |
| `2026-09-30;balance;Caixinha Exemplo;;1.012,34;;;;;;;;;1.020,00` | Valuation of the investment: net 1.012,34, gross 1.020,00 (estimated tax 7,66). |

The dry run of that file on a scratch database (counts and totals only):

```text
Importação por CSV — plano (nada foi gravado no banco)
Arquivo: 14 linha(s) de dados · separador “;” · SHA-256 c75957fb0a215b3b…
Linhas por tipo: despesas 6 · receitas 1 · estornos 2 · transferências 3 · saldos 2
Serão criados: 23 lançamento(s) (parcelas e pernas de transferência incluídas) · compras parceladas: 2 · pagamentos de fatura: 1 · saldos informados: 2
Totais por conta (soma dos lançamentos que serão criados):
  Caixinha Exemplo · 1 lançamento(s) · R$ 1.000,00
  Cartão Exemplo · 14 lançamento(s) · -R$ 2.770,90
  Conta Corrente · 7 lançamento(s) · R$ 2.235,10
  Conta Reserva · 1 lançamento(s) · R$ 300,00
Faturas tocadas:
  Cartão Exemplo · fatura ago/2026 (fecha 25/08 · vence 05/09, nova): 1 compra(s)/estorno(s) · 1 pagamento(s) · compras líquidas R$ 150,00 · pagos R$ 150,00
  Cartão Exemplo · fatura set/2026 (fecha 24/09 · vence 05/10, nova): 4 compra(s)/estorno(s) · 0 pagamento(s) · compras líquidas R$ 470,24 · pagos R$ 0,00
  ...
Fatura de cada lançamento de cartão: depois do fechamento 1 · antes do fechamento 3 · fatura escolhida por você 1
Avisos: nenhum
Nada foi gravado. Para gravar: acrescente --apply (um backup é feito antes).
```

## 9. Reading the plan (dry-run report)

- **Linhas por tipo:** rows of the file per kind.
- **Serão criados:** transactions that will exist (one per installment, two per transfer between
  tracked accounts, one per one-leg transfer, two per payment from a tracked account), how many
  installment purchases, payments and balances.
- **Totais por conta:** per account, the number of transactions and their signed sum. Check them
  against your own arithmetic: this is the most useful line.
- **Faturas tocadas:** each statement that gets entries or payments, with its closing and due dates,
  whether it already exists or the file creates it, entries, payments, net purchases and amounts paid.
- **Fatura de cada lançamento de cartão:** why each (first) card entry went where it went.
- **Avisos:** things worth a look but not errors.

The report never shows descriptions or per-entry amounts (so it is safe to paste when asking for help).

## 10. Error codes

Printed as `linha L · coluna C: [CODE] message` (in Portuguese). Codes starting with a domain name of
the app (for example `ACCOUNT_INACTIVE`) are the same as in the app.

### 10.1 File and header

| Code | Meaning | How to fix |
|---|---|---|
| `FILE_NOT_UTF8` | Not UTF-8 (or has NUL bytes). | Save as "CSV UTF-8". |
| `FILE_EMPTY` | No header line. | Start from `--template`. |
| `DELIMITER_AMBIGUOUS` | Two delimiters fit the header equally. | Pass `--delimiter`. |
| `UNTERMINATED_QUOTE` | An opening `"` never closes. | Close it or double it (`""`). |
| `MALFORMED_ROW` | Text right after a closing quote, or a broken record. | Quote the whole field. |
| `TOO_MANY_ROWS` | More than 20,000 data rows. | Split the file. |
| `UNKNOWN_COLUMN` | A header name that is not a column or alias. | Fix the typo (see section 4.2). |
| `DUPLICATE_COLUMN` | A column twice. | Remove one. |
| `MISSING_COLUMN` | `date`, `kind`, `account` or `amount` missing. | Add it. |
| `UNNAMED_COLUMN` | A value under an empty header cell. | Name the column or empty the cells. |
| `ROW_WIDTH` | More or fewer fields than the header. | An unquoted delimiter inside a field? Quote it. |

### 10.2 Cell values

| Code | Meaning | How to fix |
|---|---|---|
| `REQUIRED_FIELD` | A cell the kind requires is empty. | Fill it (section 4.2). |
| `FIELD_TOO_LONG` | Description over 300 or notes over 2,000 characters. | Shorten. |
| `INVALID_DATE` | Not `YYYY-MM-DD` / `dd/mm/yyyy`, or the date does not exist. | Fix the date; 4-digit year. |
| `DATE_TOO_FAR` | More than 366 days after today. | Probably a typo in the year. |
| `DATE_TOO_OLD` | Before 1900. | Typo in the year. |
| `INVALID_AMOUNT` | Not a money value. | Section 5.1. |
| `AMOUNT_AMBIGUOUS` | `1.234` / `1,234`. | Write `1234,00` or `1.234,00`. |
| `AMOUNT_TOO_MANY_DECIMALS` | More than 2 decimals. | Round it yourself. |
| `AMOUNT_TOO_LARGE` | R$ 10 billion or more. | Typo. |
| `AMOUNT_NOT_POSITIVE` | Zero (balances may be zero). | Remove the row or fix the value. |
| `SIGN_KIND_MISMATCH` | The sign contradicts the kind. | Section 5.2. |
| `INVALID_BOOLEAN` | Not a yes/no value. | `sim`/`não`, `yes`/`no`, `1`/`0`, `x`, blank. |
| `INVALID_CHOICE` | Unknown `kind` or `amount_type`. | Use the listed values. |
| `INVALID_YEAR_MONTH` | `statement` is not `YYYY-MM`. | `2026-09`, not `09/2026`. |
| `INVALID_NUMBER` | `installments`/`installment_number` is not a plain integer. | Digits only. |
| `INVALID_INSTALLMENT_COUNT` | `installments` outside 1 to 120. | Fix. |
| `INSTALLMENT_OUT_OF_RANGE` | `installment_number` larger than `installments` (or 0). | Fix. |
| `NOT_ALLOWED_FOR_KIND` | A column filled that the kind does not take. | Leave it blank (table in 4.2). |
| `REFUND_NOT_INSTALLMENT` | Installments on a refund. | Enter the refund as one row. |
| `INSTALLMENT_DETAILS_WITHOUT_COUNT` | `installment_number` or `amount_type` without `installments` of 2 or more. | Add `installments` or clear them. |
| `INSTALLMENT_NOT_RECURRING` | `recurring` true on an installment purchase. | Clear `recurring`. |

### 10.3 Business rules

| Code | Meaning | How to fix |
|---|---|---|
| `UNKNOWN_ACCOUNT` | No account with that nickname. | See the "Parecidas" list; create the account first if needed. |
| `AMBIGUOUS_ACCOUNT` | Two accounts have the same nickname (ignoring case and accents). | Rename one in the app. |
| `UNKNOWN_CATEGORY` | No category with that name or slug. | See the list; create it first. |
| `AMBIGUOUS_CATEGORY` | The name matches two categories. | Use the slug, or rename one. |
| `ACCOUNT_INACTIVE` | The account is deactivated. | Reactivate it or use another. |
| `ACCOUNT_KIND_NOT_ALLOWED` | The kind of account does not take this row (table in 7.1). | E.g. income on a card, expense on an investment, a transfer *out of* a card. |
| `CATEGORY_KIND_MISMATCH` | Category kind disagrees with the entry kind. | Pick a category of the right kind. |
| `TRANSFER_NEEDS_ACCOUNT` | `account` and `to_account` both blank. | Fill at least one. |
| `TRANSFER_SAME_ACCOUNT` | Same account both sides. | Fix. |
| `TRANSFER_CATEGORY_FIXED` | A neutral category other than `transfer` on a transfer. | Leave `category` blank. |
| `STATEMENT_ONLY_FOR_CARDS` | `statement` on a non-card row. | Clear it. |
| `INSTALLMENTS_ONLY_ON_CARDS` | Installments (2+) on a non-card account. | Use a card or clear. |
| `STATEMENT_REQUIRED` | A payment or a running installment (`installment_number` > 1) without `statement`. | Fill `statement`. |
| `STATEMENT_NOT_FOUND` | The paid statement does not exist and the file creates no entry on it. | Add the purchases to the file, or enter the right month. |
| `STATEMENT_ALREADY_PAID` | An entry would land on a paid (locked) statement. | Choose the right statement/date; paid statements are history. |
| `PAYMENT_NOTES_NOT_SUPPORTED` | `notes` on a payment. | Clear it. |
| `HOLDING_REQUIRED` | Transfer to or from an investment account that tracks holdings. | Not supported here. |
| `ACCOUNT_TRACKS_HOLDINGS` | Balance on such an account. | Not supported here. |
| `BALANCE_NOT_FOR_CARDS` | Balance on a card. | Use statement totals in the app. |
| `GROSS_ONLY_FOR_INVESTMENTS` | `gross_amount` on a non-investment account. | Clear it. |
| `INVALID_GROSS_BALANCE` | Gross below net. | Fix. |
| `DUPLICATE_BALANCE` | Two balances for one account and date in the file. | Keep one. |
| `INSTALLMENT_AMOUNT_TOO_SMALL` | The total does not give each installment a cent. | Fix the amount or the count. |
| `FEED_PLAN_MISMATCH` | Internal check at apply time: the result differed from the plan. The database is untouched. | Report it. |

### 10.4 Warnings (do not stop anything)

| Code | Meaning |
|---|---|
| `DUPLICATE_ROWS` | N rows are identical to an earlier one. They are kept. |
| `STATEMENT_OVERRIDES_CYCLE` | Your `statement` differs from what the card cycle gives for that date. |
| `PAYMENT_ABOVE_OUTSTANDING` | Payments exceed what the statement owes. |
| `BALANCE_REPLACES_EXISTING` | A balance already exists for that account and date. |

## 11. Safety model

- **Dry run is the default.** Without `--apply` nothing is written: not the database, not a backup,
  not the ledger.
- **Parse and validate everything first.** One bad row means zero rows imported.
- **`--apply` steps:**
  1. refuse if this exact file was already applied (below);
  2. re-run the checks;
  3. **backup** with the app's own mechanism (`data/backups/financas-<timestamp>/`, the SQLite
     backup API plus `data/images/`);
  4. copy the database to `data/import/csv_work.db` and run **everything on the copy**, through the
     use cases (entries, transfers and card purchases in file order, then statement payments, then
     balances);
  5. **verify** on the copy, per account (transactions created and their sum equal the plan), per
     statement (entries and sum), transfers (at most two legs, opposite amounts), card entries linked to
     a statement, balances recorded, and that each installment purchase came out as planned;
  6. only if every check passes, replace the real database with the copy. Any failure (a rule broken
     at run time, an unexpected error, a failed check) discards the copy, leaves the real database
     untouched and exits with code 3.
- **SHA-256 guard.** The file's SHA-256 is recorded after a successful apply in
  `data/import/csv_applied.jsonl`, one JSON line each: `sha256`, `applied_at`, `counts` (numbers) and
  `forced`. The same bytes are refused (exit 4) unless you pass `--force`. Changing a single character
  of the file gives a new hash, so the guard stops accidental re-runs, not deliberate ones. Note that
  even with `--force` the rules still apply: a file whose payment fully paid a statement cannot be
  applied again, because its purchases would land on a paid statement.
- **Restore a backup** (if you want to undo a successful apply): stop the server, then
  ```bash
  cp data/backups/financas-YYYYMMDD-HHMMSS/financas.db data/financas.db
  rm -f data/financas.db-wal data/financas.db-shm
  ```
  (and remove the matching line from `data/import/csv_applied.jsonl` if you want to apply the same
  file again). Entries can also be deleted one by one in the app.
- **What is logged, and what never is.** Printed reports and the ledger contain counts, totals, line
  numbers, column names, codes and statement months. **Never** descriptions, notes, or the amount of an
  individual entry. An unexpected failure goes to the app's failure log (`financas log show`) with the
  exception type and location, not row contents. The CSV itself is *your* file: it contains real
  data, so keep it out of git (`*.csv` is git-ignored except the synthetic example) and out of
  cloud-synced folders.
- **One writer.** Do not run the server and the script's `--apply` at the same time.
- If the database is behind the code, a dry run asks you to migrate first; `--apply` migrates the
  working copy (after the backup).

## 12. Preparing the CSV from Excel or Google Sheets

1. Put the header from `--template` in row 1 (any order of columns; Portuguese names are fine).
2. Format `date` as text or use `dd/mm/yyyy`/ISO; if Excel turns `2026-09-30` into a date, the
   export writes it in the regional format (`30/09/2026`), which is also accepted. Use 4-digit years.
3. Write amounts as you would in pt-BR (`1.234,56`) or plain (`1234,56`). If you use formulas, convert to
   values first. Do not write `1.234` meaning one thousand (write `1234,00`).
4. Format `statement` as **text** so `2026-09` stays `2026-09` (Excel tends to make it `set/26`).
5. Export: Excel: *Save As → CSV UTF-8 (Comma delimited)*; despite the name, Excel in pt-BR uses
   `;`. Google Sheets: *File → Download → Comma-separated values (.csv)*, which uses `,`.
6. Open the file in a text editor and look at the first line to check the delimiter; the tool detects
   it, but `--delimiter` overrides.
7. One row per entry; leave unused cells empty; do not merge cells or leave totals rows (a total row
   would be read as an entry). Notes in `#` lines are allowed.
8. Run the dry run.

## 13. Pre-apply checklist

- [ ] The dry run printed no problems and the **totals per account** match your own sums.
- [ ] Each card statement touched is the one you expect (look at "Faturas tocadas" and the reasons).
- [ ] The warnings were read (duplicates, chosen statements, overpayments, replaced balances).
- [ ] Installment purchases: total or per-installment amount chosen correctly; running ones have the
      right current installment and statement.
- [ ] Transfers have the right direction (`account` pays, `to_account` receives).
- [ ] The server is stopped.
- [ ] You know where the backup will be (`data/backups/`).
- [ ] The file is the final one (the hash will be recorded).

## 14. Troubleshooting and FAQ

- **"Banco de dados não encontrado"** (exit 1): run `uv run financas init`, or check `FINANCAS_DB_URL`
  / `FINANCAS_DATA_DIR`.
- **Every row says `UNKNOWN_ACCOUNT`:** the nickname differs from the one in the app; compare with
  `uv run financas account list`. Case and accents do not matter, other characters do.
- **`AMOUNT_AMBIGUOUS` on something like `2.500`:** write `2500,00` or `2.500,00`.
- **Excel changed my `2026-09` into a date:** format the column as text before typing, or write
  `statement` with a leading apostrophe.
- **A transfer shows one leg only:** one of the two accounts was blank. That is intentional for
  accounts the system does not track.
- **A card purchase went to a different statement than the bank's:** your issuer behaves differently
  from the closing rule. Fill `statement` on that row. The plan lists every override.
- **A purchase dated the closing day went to the next statement:** that is the rule (CLAUDE.md 9.3);
  the owner's three cards behave that way.
- **`STATEMENT_ALREADY_PAID` on a row dated long ago:** the cycle puts it in a statement that was
  paid. Fill `statement` with the right (open) month or enter it in the app.
- **The same file was applied twice by mistake:** the guard stops it. If it happened with
  `--force`, restore the backup (section 11).
- **I applied and the balance difference is large:** that is the point of the report; it means
  entries are missing (checking) or it is the yield (investment). Nothing is posted automatically.
- **Can I import 2025?** Dates may be any day from 1900 to a year ahead; the rules are the same.
- **Why does it not create accounts/categories?** To keep typos from creating phantom accounts. A
  misspelled name is an error with suggestions.
- **Where is the web option?** Later. It will use the same parsing, validation and planning code
  (`src/financas/application/csvfeed/`), so files that work here will work there.

## 15. For developers

Pure code, no I/O, no Portuguese, in `src/financas/application/csvfeed/`: `parsing` (cells),
`reader` (text → rows), `validate` (row rules), `planner` (business rules against a `FeedContext`),
`apply` (use cases and verification), `context` (reads the database), `template`. Public entry
points: `parse_feed`, `plan_feed`, `analyze_feed`, `load_context`, `ApplyFeed`, `snapshot`,
`verify_feed`, `template_text`. Error codes are stable ASCII strings; pt-BR wording is in
`src/financas/interfaces/messages/csvfeed.py`, the report in `src/financas/interfaces/csvfeed.py`,
the script in `scripts/feed_from_csv.py` (outside the layers, like `cli.py`). Tests:
`tests/unit/test_csvfeed_*.py` and `tests/integration/test_feed_script.py`.
