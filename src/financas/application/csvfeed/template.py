"""The ``--template`` text (and the file the web page offers to download): the header plus commented
example rows, one per kind and one for each optional column (payment method, merchant, refunded
purchase, itemized purchase on a card or on a PIX)."""

from financas.application.csvfeed.model import COLUMN_ORDER, FeedColumn

TEMPLATE_DELIMITER = ";"

C = FeedColumn

# Accounts and categories must exist in the database. A row lists only the columns it fills.
_EXAMPLES: tuple[dict[FeedColumn, str], ...] = (
    # a standard expense, with the merchant
    {
        C.DATE: "2026-10-05",
        C.KIND: "expense",
        C.ACCOUNT: "Checking",
        C.AMOUNT: "-45,90",
        C.DESCRIPTION: "Pharmacy",
        C.CATEGORY: "Health",
        C.RECURRING: "no",
        C.PAYMENT_METHOD: "debito",
        C.MERCHANT: "Drogasil",
    },
    # a boleto and a PIX: the payment method is optional (blank: from the wording, else PIX)
    {
        C.DATE: "2026-10-06",
        C.KIND: "expense",
        C.ACCOUNT: "Checking",
        C.AMOUNT: "-650,00",
        C.DESCRIPTION: "Building fee",
        C.CATEGORY: "Home",
        C.PAYMENT_METHOD: "boleto",
    },
    # a PIX split in two: the first row is the PIX (total, no category), the next rows that share
    # its group are the items (description, category, amount) and add up to the total
    {
        C.DATE: "2026-10-04",
        C.KIND: "expense",
        C.ACCOUNT: "Checking",
        C.AMOUNT: "-180,00",
        C.DESCRIPTION: "Saturday market",
        C.PAYMENT_METHOD: "pix",
        C.GROUP: "pix-1",
    },
    {
        C.AMOUNT: "110,00",
        C.DESCRIPTION: "Greens and fruit",
        C.CATEGORY: "Groceries",
        C.GROUP: "pix-1",
    },
    {
        C.AMOUNT: "70,00",
        C.DESCRIPTION: "Butcher",
        C.CATEGORY: "Groceries",
        C.GROUP: "pix-1",
    },
    # a standard income
    {
        C.DATE: "2026-10-05",
        C.KIND: "income",
        C.ACCOUNT: "Checking",
        C.AMOUNT: "3.500,00",
        C.DESCRIPTION: "Salary October",
        C.CATEGORY: "Salary",
        C.RECURRING: "yes",
    },
    {
        C.DATE: "2026-10-06",
        C.KIND: "refund",
        C.ACCOUNT: "Card",
        C.AMOUNT: "12,50",
        C.DESCRIPTION: "Store refund",
        C.STATEMENT: "2026-10",
    },
    {
        C.DATE: "2026-10-07",
        C.KIND: "transfer",
        C.ACCOUNT: "Checking",
        C.TO_ACCOUNT: "Savings",
        C.AMOUNT: "500,00",
        C.DESCRIPTION: "Monthly contribution",
    },
    {
        C.DATE: "2026-10-08",
        C.KIND: "expense",
        C.ACCOUNT: "Card",
        C.AMOUNT: "1.200,00",
        C.DESCRIPTION: "Notebook",
        C.CATEGORY: "Shopping",
        C.INSTALLMENTS: "3",
        C.AMOUNT_TYPE: "total",
    },
    {
        C.DATE: "2026-10-08",
        C.KIND: "expense",
        C.ACCOUNT: "Card",
        C.AMOUNT: "100,00",
        C.DESCRIPTION: "Course (running)",
        C.CATEGORY: "Education",
        C.STATEMENT: "2026-11",
        C.INSTALLMENTS: "10",
        C.INSTALLMENT_NUMBER: "3",
        C.AMOUNT_TYPE: "installment",
    },
    {
        C.DATE: "2026-10-15",
        C.KIND: "transfer",
        C.ACCOUNT: "Checking",
        C.TO_ACCOUNT: "Card",
        C.AMOUNT: "850,00",
        C.DESCRIPTION: "Card payment",
        C.STATEMENT: "2026-10",
    },
    {
        C.DATE: "2026-10-31",
        C.KIND: "balance",
        C.ACCOUNT: "Savings",
        C.AMOUNT: "10.250,00",
        C.DESCRIPTION: "Month end",
        C.GROSS_AMOUNT: "10.400,00",
    },
    # a refunded purchase: kept on record, counted nowhere
    {
        C.DATE: "2026-10-10",
        C.KIND: "expense",
        C.ACCOUNT: "Checking",
        C.AMOUNT: "89,90",
        C.DESCRIPTION: "Headphones (returned)",
        C.CATEGORY: "Shopping",
        C.MERCHANT: "Amazon",
        C.REFUNDED: "yes",
    },
    # an itemized purchase: the first row is the purchase (total, no category), the next rows
    # that share its group are the items (description, category, amount); they add up to the total
    {
        C.DATE: "2026-10-09",
        C.KIND: "expense",
        C.ACCOUNT: "Card",
        C.AMOUNT: "450,00",
        C.DESCRIPTION: "Gaming setup",
        C.MERCHANT: "Kabum",
        C.GROUP: "order-1",
    },
    {
        C.AMOUNT: "350,00",
        C.DESCRIPTION: "Gaming monitor",
        C.CATEGORY: "Shopping",
        C.GROUP: "order-1",
    },
    {
        C.AMOUNT: "100,00",
        C.DESCRIPTION: "HDMI cable and stand",
        C.CATEGORY: "Home",
        C.GROUP: "order-1",
    },
)


def _line(example: dict[FeedColumn, str]) -> str:
    return TEMPLATE_DELIMITER.join(example.get(column, "") for column in COLUMN_ORDER)


def template_text() -> str:
    """Header line and commented example rows (``# ...``), semicolon-delimited."""
    header = TEMPLATE_DELIMITER.join(c.value for c in COLUMN_ORDER)
    lines = [header]
    lines.extend(f"# {_line(example)}" for example in _EXAMPLES)
    return "\n".join(lines) + "\n"
