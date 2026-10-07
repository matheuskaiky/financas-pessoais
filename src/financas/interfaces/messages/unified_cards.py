"""pt-BR wording and URLs of the unified cards view (``/cards?card=all``): chips and live text."""

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from financas.application.queries.cards import CardView

ALL_LABEL = "Todos"
KEEP_ONE = "Mantenha ao menos um cartão selecionado."


@dataclass(frozen=True)
class Chip:
    id: str  # stable: HTMX restores the focus to it after the swap
    label: str
    pressed: bool
    disabled: bool  # the last included card cannot be excluded
    url: str  # the page after this chip's own toggle
    card_id: str | None = None


def unified_url(order: Sequence[str], included: Sequence[str]) -> str:
    """``/cards?card=all`` for every card, ``&cards=a,b`` (face order) for a subset."""
    chosen = [card_id for card_id in order if card_id in set(included)]
    if len(chosen) == len(order):
        return "/cards?card=all"
    return "/cards?" + urlencode({"card": "all", "cards": ",".join(chosen)}, safe=",")


def unified_chips(cards: Sequence[CardView], included: Sequence[str]) -> list[Chip]:
    order = [v.account.id for v in cards]
    every = len(included) == len(order)
    chips = [Chip("chip-all", ALL_LABEL, every, False, unified_url(order, order))]
    for view in cards:
        card_id = view.account.id
        on = card_id in included
        after = [i for i in included if i != card_id] if on else [*included, card_id]
        chips.append(
            Chip(
                f"chip-{card_id}",
                view.account.nickname,
                on,
                disabled=on and len(included) == 1,
                url=unified_url(order, after or list(included)),
                card_id=card_id,
            )
        )
    return chips


def unified_live_text(names: Sequence[str], every: bool) -> str:
    """ "Mostrando todos os cartões." / "Mostrando Nubank e Itaú." (no amounts in live text)."""
    if every:
        return "Mostrando todos os cartões."
    if len(names) == 1:
        return f"Mostrando {names[0]}."
    return f"Mostrando {', '.join(names[:-1])} e {names[-1]}."
