"""fp_money — formatação de moeda no servidor (Jinja2). Espelho de fp-money.js: mesmas regras, mesmos resultados.

Só biblioteca padrão (Jinja2/MarkupSafe já vêm com o app). Dinheiro entra em UNIDADE MENOR INTEIRA (centavos no BRL),
nunca em float. Valor ausente (None) não vira zero: sai o `fallback` ("—").

Os dois lados são testados contra tests/golden/money.json, gerado pelo Intl do Node (tests/make-golden.mjs):
se JS e Python divergirem, o teste quebra. Para aceitar um locale ou divisa novos, veja o cabeçalho de make-golden.mjs.

Uso no app (FastAPI):

    from fastapi.templating import Jinja2Templates
    import fp_money

    templates = Jinja2Templates(directory="app/templates")
    fp_money.install(templates.env, locale="pt-BR", currency="BRL")      # filtros + global `config`

No template (nenhum "R$" escrito à mão):

    {{ lancamento.valor_centavos | currency }}                    →  R$ 14.850,00   (texto: title, aria-label, e-mail)
    {{ lancamento.valor_centavos | currency(sign="always") }}     →  +R$ 350,00
    {{ saldo_centavos | money }}                                  →  <span class="money">…símbolo e número em spans…</span>
    {{ saldo_centavos | money(size="display") }}                  →  símbolo reduzido, para números grandes
    {{ config.currency_symbol }}                                  →  R$     (rótulos de formulário: "Valor ({{ config.currency_symbol }})")

No <head>, para o JavaScript usar a mesma divisa, um bloco de DADOS (não executa: vale mesmo com CSP estrita, sem 'unsafe-inline'):

    <script type="application/json" id="app-config">{{ config.client | tojson }}</script>

O fp-money.js lê esse bloco ao carregar. (Com um <script> próprio também vale: window.APP_CONFIG = {{ config.client | tojson }};)
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from markupsafe import Markup

__all__ = [
    "APP_CONFIG",
    "MINUS",
    "configure",
    "currency_symbol",
    "format_amount",
    "format_currency",
    "format_money_parts",
    "get_config",
    "install",
    "minor_digits",
    "money_html",
    "parse_decimal_to_minor",
]

MINUS = "\u2212"

# Valor padrão no topo do arquivo; `configure()` ou `install()` mudam. Equivale ao window.APP_CONFIG do navegador.
APP_CONFIG: dict[str, str] = {"locale": "pt-BR", "currency": "BRL"}


@dataclass(frozen=True)
class _Rule:
    dec: str  # separador decimal
    group: str  # separador de milhar
    after: bool  # símbolo depois do número?
    gap: str  # espaço entre símbolo e número
    min_group: int  # 1: agrupa já em 1.234; 2: só a partir de 12.345 (pt-PT, es-ES)


# Tabelas geradas por `node tests/make-golden.mjs --py` (CLDR via Intl). Não edite à mão: gere de novo.
LOCALES: dict[str, _Rule] = {
    "pt-BR": _Rule(dec=",", group=".", after=False, gap="\u00a0", min_group=1),
    "pt-PT": _Rule(dec=",", group="\u00a0", after=True, gap="\u00a0", min_group=2),
    "en-US": _Rule(dec=".", group=",", after=False, gap="", min_group=1),
    "en-GB": _Rule(dec=".", group=",", after=False, gap="", min_group=1),
    "de-DE": _Rule(dec=",", group=".", after=True, gap="\u00a0", min_group=1),
    "fr-FR": _Rule(dec=",", group="\u202f", after=True, gap="\u00a0", min_group=1),
    "es-ES": _Rule(dec=",", group=".", after=True, gap="\u00a0", min_group=2),
}
SYMBOLS: dict[tuple[str, str], str] = {
    ("BRL", "pt-BR"): "R$",
    ("USD", "pt-BR"): "US$",
    ("EUR", "pt-BR"): "€",
    ("GBP", "pt-BR"): "£",
    ("JPY", "pt-BR"): "JP¥",
    ("BRL", "pt-PT"): "R$",
    ("USD", "pt-PT"): "US$",
    ("EUR", "pt-PT"): "€",
    ("GBP", "pt-PT"): "£",
    ("JPY", "pt-PT"): "JP¥",
    ("BRL", "en-US"): "R$",
    ("USD", "en-US"): "$",
    ("EUR", "en-US"): "€",
    ("GBP", "en-US"): "£",
    ("JPY", "en-US"): "¥",
    ("BRL", "en-GB"): "R$",
    ("USD", "en-GB"): "US$",
    ("EUR", "en-GB"): "€",
    ("GBP", "en-GB"): "£",
    ("JPY", "en-GB"): "JP¥",
    ("BRL", "de-DE"): "R$",
    ("USD", "de-DE"): "$",
    ("EUR", "de-DE"): "€",
    ("GBP", "de-DE"): "£",
    ("JPY", "de-DE"): "¥",
    ("BRL", "fr-FR"): "R$",
    ("USD", "fr-FR"): "$US",
    ("EUR", "fr-FR"): "€",
    ("GBP", "fr-FR"): "£GB",
    ("JPY", "fr-FR"): "JPY",
    ("BRL", "es-ES"): "BRL",
    ("USD", "es-ES"): "US$",
    ("EUR", "es-ES"): "€",
    ("GBP", "es-ES"): "GBP",
    ("JPY", "es-ES"): "JPY",
}
DIGITS: dict[str, int] = {"BRL": 2, "USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}


# ───────────────────────────── configuração ─────────────────────────────


def configure(*, locale: str | None = None, currency: str | None = None) -> dict[str, str]:
    """Muda a divisa/locale do app inteiro (equivale a atribuir window.APP_CONFIG)."""
    cfg = get_config()
    if locale:
        cfg["locale"] = locale
    if currency:
        cfg["currency"] = currency.upper()
    _check(cfg["locale"], cfg["currency"])
    APP_CONFIG.update(cfg)
    return get_config()


def get_config() -> dict[str, str]:
    return {
        "locale": str(APP_CONFIG.get("locale") or "pt-BR"),
        "currency": str(APP_CONFIG.get("currency") or "BRL").upper(),
    }


def _check(locale: str, currency: str) -> None:
    if locale not in LOCALES:
        raise ValueError(
            f"locale sem regra em fp_money.LOCALES: {locale!r} (aceitos: {', '.join(LOCALES)}). Veja tests/make-golden.mjs."
        )
    if currency not in DIGITS:
        raise ValueError(
            f"divisa sem regra em fp_money.DIGITS: {currency!r} (aceitas: {', '.join(DIGITS)})."
        )


def _resolve(locale: str | None, currency: str | None) -> tuple[str, str]:
    cfg = get_config()
    loc, cur = locale or cfg["locale"], (currency or cfg["currency"]).upper()
    _check(loc, cur)
    return loc, cur


def minor_digits(currency: str | None = None) -> int:
    """Casas da unidade menor: BRL/USD/EUR = 2, JPY = 0."""
    return DIGITS[(currency or get_config()["currency"]).upper()]


# ───────────────────────────── formatação ─────────────────────────────


def _group(whole: str, rule: _Rule) -> str:
    if len(whole) < 4 or (rule.min_group == 2 and len(whole) < 5):
        return whole
    out: list[str] = []
    while len(whole) > 3:
        out.insert(0, whole[-3:])
        whole = whole[:-3]
    out.insert(0, whole)
    return rule.group.join(out)


def _as_minor(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return None
            return round(value)
        return int(value)
    except (TypeError, ValueError):
        return None


def _places(cur: str, places: int | None) -> int:
    """Casas decimais do valor: as da divisa, ou `places` (o inteiro vale 10^-places da unidade: custo de API em frações de centavo)."""
    return DIGITS[cur] if places is None else max(0, min(8, int(places)))


def format_money_parts(
    minor: Any,
    *,
    locale: str | None = None,
    currency: str | None = None,
    sign: str = "auto",
    symbol: bool = True,
    places: int | None = None,
) -> dict[str, Any] | None:
    """Peças do valor: {neg, sign, symbol, gap, amount, position, text, minor}. None se `minor` não for número."""
    n = _as_minor(minor)
    if n is None:
        return None
    loc, cur = _resolve(locale, currency)
    rule, digits = LOCALES[loc], _places(cur, places)
    whole, frac = divmod(abs(n), 10**digits)
    amount = _group(str(whole), rule) + (rule.dec + str(frac).zfill(digits) if digits else "")
    sym = SYMBOLS.get((cur, loc), cur)
    neg = n < 0
    sgn = "" if sign == "never" else MINUS if neg else "+" if (sign == "always" and n > 0) else ""
    if not symbol or not sym:
        body = amount
    else:
        body = amount + rule.gap + sym if rule.after else sym + rule.gap + amount
    return {
        "neg": neg,
        "sign": sgn,
        "symbol": sym,
        "gap": rule.gap,
        "amount": amount,
        "position": "after" if rule.after else "before",
        "text": sgn + body,
        "minor": n,
    }


def format_currency(
    minor: Any,
    locale: str | None = None,
    currency: str | None = None,
    *,
    sign: str = "auto",
    symbol: bool = True,
    fallback: str = "—",
    places: int | None = None,
) -> str:
    """Texto completo com símbolo: 1485000 → "R$ 14.850,00"; -15000 → "−R$ 150,00"; None → "—"."""
    p = format_money_parts(
        minor, locale=locale, currency=currency, sign=sign, symbol=symbol, places=places
    )
    return p["text"] if p else fallback


def format_amount(
    minor: Any,
    locale: str | None = None,
    currency: str | None = None,
    *,
    sign: str = "auto",
    fallback: str = "—",
    places: int | None = None,
) -> str:
    """Só o número ("14.850,00"). Para campos de formulário."""
    return format_currency(
        minor, locale, currency, sign=sign, symbol=False, fallback=fallback, places=places
    )


def currency_symbol(locale: str | None = None, currency: str | None = None) -> str:
    loc, cur = _resolve(locale, currency)
    return SYMBOLS.get((cur, loc), cur)


def parse_decimal_to_minor(text: str, currency: str | None = None) -> int | None:
    """ "14850.00" → 1485000. Decimal em texto, sem float. Mais casas que a divisa tem: arredonda (meio para cima)."""
    import re

    m = re.fullmatch(r"\s*([+-])?(\d+)(?:\.(\d+))?\s*", str(text))
    if not m:
        return None
    d = minor_digits(currency)
    frac = (m.group(3) or "").ljust(d + 1, "0")
    v = int(m.group(2) + frac[:d]) + (1 if int(frac[d] or 0) >= 5 else 0)
    return -v if m.group(1) == "-" else v


def money_html(
    minor: Any,
    locale: str | None = None,
    currency: str | None = None,
    *,
    sign: str = "auto",
    size: str | None = None,
    css_class: str = "",
    bare: bool = False,
    places: int | None = None,
    private: str | None = None,
) -> Markup:
    """Mesmo HTML que o hidratador do navegador (fp-money.js) escreve; `data-cents` deixa o cliente refazer se a divisa mudar.
    Tudo que foge do padrão (sinal, sem símbolo, outra divisa/locale/casas) vai em data-*, para o cliente refazer IGUAL.
    `private` (opt-in, ausente por padrão): grupo do modo privacidade (networth, accounts, investments, cards, transactions);
    escreve data-private="<grupo>" no invólucro, e é só isso: o desfoque vem do tokens.css quando o usuário liga o modo.
    Valor ausente («—») não leva o atributo: não há o que esconder."""
    p = format_money_parts(
        minor, locale=locale, currency=currency, sign=sign, symbol=not bare, places=places
    )
    cls = " ".join(c for c in ("money", f"money--{size}" if size else "", css_class) if c)
    if p is None:
        return Markup(f'<span class="{escape(cls)} money--empty">—</span>')
    bits: list[str] = []
    if p["sign"]:
        bits.append(f'<span class="money-sign">{escape(p["sign"])}</span>')
    amt = f'<span class="currency-amount">{escape(p["amount"])}</span>'
    if bare or not p["symbol"]:
        bits.append(amt)
    else:
        # o respiro entre símbolo e número fica DENTRO do span do símbolo (encolhe junto nos números grandes)
        bits.append(
            f'<span class="currency-symbol">{escape(p["symbol"] + p["gap"])}</span>' + amt
            if p["position"] == "before"
            else amt + f'<span class="currency-symbol">{escape(p["gap"] + p["symbol"])}</span>'
        )
    n = p["minor"]
    data = ""
    if sign and sign != "auto":
        data += f' data-sign="{escape(sign)}"'
    if bare:
        data += " data-bare"
    if currency:
        data += f' data-currency="{escape(currency.upper())}"'
    if locale:
        data += f' data-locale="{escape(locale)}"'
    if places is not None:
        data += f' data-places="{int(places)}"'
    if private:
        data += f' data-private="{escape(private)}"'
    return Markup(
        f'<span class="{escape(cls)}"{" data-neg" if p["neg"] else ""} data-cents="{n}"{data}>{"".join(bits)}</span>'
    )


# ───────────────────────────── Jinja2 ─────────────────────────────


class _Config:
    """O `config` dos templates: sempre reflete a divisa em vigor."""

    @property
    def locale(self) -> str:
        return get_config()["locale"]

    @property
    def currency(self) -> str:
        return get_config()["currency"]

    @property
    def currency_symbol(self) -> str:
        return currency_symbol()

    @property
    def currency_position(self) -> str:
        return "after" if LOCALES[self.locale].after else "before"

    @property
    def minor_digits(self) -> int:
        return minor_digits()

    @property
    def client(self) -> dict[str, str]:
        """Para o bloco `<script type="application/json" id="app-config">{{ config.client | tojson }}</script>` no <head>."""
        return get_config()


def install(env: Any, *, locale: str | None = None, currency: str | None = None) -> None:
    """Registra no ambiente Jinja2: filtros `currency`, `amount`, `money` e o global `config`."""
    if locale or currency:
        configure(locale=locale, currency=currency)
    env.filters["currency"] = lambda v, **kw: format_currency(v, **kw)
    env.filters["amount"] = lambda v, **kw: format_amount(v, **kw)
    env.filters["money"] = lambda v, **kw: money_html(v, **kw)
    env.globals["config"] = _Config()
