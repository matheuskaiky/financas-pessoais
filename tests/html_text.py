"""Text of an HTML page, for assertions on amounts.

Amounts are written as ``<span class="money">`` markup (sign, symbol and digits in their own
spans) and odometers carry the amount in ``aria-label``. ``visible`` returns the page twice: the
raw HTML (attributes, so ``aria-label="R$ 1,00"`` still matches) and the text with the tags
stripped (so a ``.money`` span reads ``R$ 1.234,56``). A no-break space reads as a space; the
typographic minus (U+2212) is kept, as the page shows it.
"""

import html
import re


def visible(response_or_text: object) -> str:
    raw = getattr(response_or_text, "text", response_or_text)
    assert isinstance(raw, str)
    body = re.sub(r"<(script|style)\b.*?</\1>", "", raw, flags=re.S)
    stripped = html.unescape(re.sub(r"<[^>]+>", "", body))
    return (raw + "\n" + stripped).replace(" ", " ")
