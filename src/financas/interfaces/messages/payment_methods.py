"""pt-BR wording of the payment methods: labels, the filter chips and the form's options."""

from financas.domain.models import PaymentMethod

PAYMENT_METHOD_LABELS: dict[PaymentMethod, str] = {
    PaymentMethod.PIX: "PIX",
    PaymentMethod.DEBIT: "Débito",
    PaymentMethod.BOLETO: "Boleto",
    PaymentMethod.TRANSFER: "Transferência",
    PaymentMethod.CASH: "Dinheiro",
    PaymentMethod.OTHER: "Outro",
    PaymentMethod.CREDIT_CARD: "Cartão de crédito",
}

# the chips above the entries list: "" is every entry
FILTER_CHIPS: tuple[tuple[str, str], ...] = (
    ("", "Todos"),
    (PaymentMethod.CREDIT_CARD.value, "Cartão de Crédito"),
    (PaymentMethod.PIX.value, "PIX"),
    (PaymentMethod.DEBIT.value, "Débito"),
    (PaymentMethod.BOLETO.value, "Boleto"),
)

# the selector of the entry forms (a checking account); other values keep their own option
FORM_OPTIONS: tuple[PaymentMethod, ...] = (
    PaymentMethod.PIX,
    PaymentMethod.DEBIT,
    PaymentMethod.BOLETO,
    PaymentMethod.OTHER,
)
DEFAULT_FORM_METHOD = PaymentMethod.PIX
