"""pt-BR wording of the "Compras Mais Caras" ranking (the live text is a sentence, no amounts)."""


def rank_live_text(top: int) -> str:
    return f"Mostrando as {top} maiores despesas."


def rank_caption(count: int) -> str:
    """The table caption read by screen readers."""
    return f"As {count} maiores despesas do período, da maior para a menor"
