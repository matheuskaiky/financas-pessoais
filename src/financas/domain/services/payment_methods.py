"""Payment method of a bank entry when the file does not say: from the bank's own wording.

Pure. A statement line like "PIX TRANSF JOAO" or "PAGTO ELETRON COBRANCA" names how the money
moved; anything else on a bank account falls back to PIX (the most common way today). The user can
always say otherwise: this only fills a blank, it never replaces a typed value.
"""

from financas.domain.models import AccountKind, PaymentMethod
from financas.domain.services.text import normalize_search

# normalised (accents and case removed) fragments, most specific first
_KEYWORDS: tuple[tuple[PaymentMethod, tuple[str, ...]], ...] = (
    (
        PaymentMethod.BOLETO,
        ("pagto eletron cobranca", "pagamento de boleto", "pagto boleto", "boleto"),
    ),
    (
        PaymentMethod.DEBIT,
        (
            "compra debito",
            "compra no debito",
            "compra cartao debito",
            "cartao debito",
            "debito em conta",
        ),
    ),
    (PaymentMethod.TRANSFER, ("ted ", " ted", "doc ", "transf ted", "transferencia ted")),
    (PaymentMethod.PIX, ("pix",)),
)


def infer_payment_method(description: str) -> PaymentMethod | None:
    """The method a description points to, or ``None`` when it points to none."""
    text = f" {normalize_search(description)} "
    for method, fragments in _KEYWORDS:
        if any(fragment in text for fragment in fragments):
            return method
    return None


def default_payment_method(account_kind: AccountKind, description: str) -> PaymentMethod | None:
    """A card purchase is ``credit_card``; a bank entry is what its description says, else PIX."""
    if account_kind is AccountKind.CREDIT_CARD:
        return PaymentMethod.CREDIT_CARD
    if account_kind is AccountKind.CHECKING:
        return infer_payment_method(description) or PaymentMethod.PIX
    return None
