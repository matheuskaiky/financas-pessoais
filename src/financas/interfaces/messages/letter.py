"""pt-BR wording of the monthly letter (Carta do mês) and the fill of its templates.

The application layer returns sentences as ``code + slots`` (``application/letter``); this module
owns every word. A template is a string with placeholders:

* ``{receitas}``: a slot, shown as the system computed it (money, percent, date...); it becomes a
  *value part* that the page links to the note of its sentence;
* ``{mes:cap}``: the same with the first letter in capitals;
* ``{n:pl(item,itens)}``: the singular or plural word for a count slot (a text part, no value).

Qualitative words come from signal slots (``comparacao`` = ``below`` -> "abaixo"). The wording is
the contract with the future assistant: it will write templates over the same placeholders and
signals, and a text with a digit it was not given is discarded (CLAUDE.md 16.4).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from financas.application.letter import Letter, Note, Section, Sentence, Slot, SlotKind, Todo
from financas.domain.money import YearMonth, format_brl
from financas.interfaces.formatting import MONTH_ABBREVIATIONS, MONTH_NAMES, format_percent

SECTION_TITLES: dict[str, str] = {
    "panorama": "Panorama",
    "where": "Onde foi o dinheiro",
    "agreed": "O que saiu do combinado",
    "ahead": "O que vem aí",
    "before": "Antes de fechar",
}

# margin heading above the chart of each section
MARGIN_TITLES: dict[str, str] = {
    "spark": "Despesas, janeiro a {mes}",
    "categories": "Maiores categorias",
    "goal": "{categoria} e a meta",
    "cash": "Caixa depois das faturas",
}

SENTENCES: dict[str, str] = {
    "panorama.empty": (
        "Não há lançamentos em {mes}. Quando houver, esta carta conta o que aconteceu "
        "com o dinheiro do mês."
    ),
    "panorama.surplus": (
        "{mes:cap} fechou no azul. Entraram {receitas} e saíram {despesas}, o que deixa "
        "um saldo de {saldo}, ou {poupanca} da renda."
    ),
    "panorama.deficit": (
        "{mes:cap} fechou no vermelho. Entraram {receitas} e saíram {despesas}: "
        "faltaram {saldo}, ou {poupanca} da renda."
    ),
    "panorama.even": "{mes:cap} fechou no zero a zero. Entraram {receitas} e saíram {despesas}.",
    "panorama.no_income": "{mes:cap} não teve receitas. Saíram {despesas}.",
    "panorama.average": "As despesas ficaram {diferenca} {comparacao} da média de {meses}.",
    "panorama.average_equal": "As despesas ficaram iguais à média de {meses}.",
    "where.none": "Não houve despesas em {mes}.",
    "where.top2": (
        "{categoria_1} e {categoria_2} levaram {soma_top}, ou {pct_top} de tudo o que saiu."
    ),
    "where.top1": "{categoria_1} levou {soma_top}, ou {pct_top} de tudo o que saiu.",
    "where.recurring": (
        "Os gastos recorrentes somam {recorrentes}, ou {pct_rec} das despesas. "
        "É a parte menos flexível do mês."
    ),
    "where.recurring_count": (
        "O custo fixo é a parte menos flexível: os {n_rec} {n_rec:pl(recorrente,recorrentes)} "
        "somam {recorrentes}, ou {pct_rec} das despesas."
    ),
    "where.recurring_one": (
        "O custo fixo é a parte menos flexível: o único recorrente soma {recorrentes}, "
        "ou {pct_rec} das despesas."
    ),
    "agreed.no_goals": (
        "Nenhuma categoria tem meta mensal definida, então não há o que comparar. "
        "As metas são definidas em Orçamento."
    ),
    "agreed.within": (
        "Todas as {n_meta} {n_meta:pl(categoria,categorias)} com meta ficaram dentro do combinado "
        "em {mes}."
    ),
    "agreed.first_over": (
        "{categoria} {severidade} em {excesso}: foram {gasto} para {meta} combinados."
    ),
    "agreed.others": (
        "{n_outras} {n_outras:pl(outra categoria também passou,outras categorias também passaram)} "
        "da meta ({nomes}), somando {soma_excessos} acima do combinado."
    ),
    "ahead.statements": (
        "Há {n_faturas} {n_faturas:pl(fatura fechada,faturas fechadas)} a pagar, "
        "somando {faturas_total}."
    ),
    "ahead.due": "A fatura do {nome}, de {valor}, vence em {vence}.",
    "ahead.more": "E mais {n_mais} {n_mais:pl(fatura fechada,faturas fechadas)}.",
    "ahead.no_statements": "Não há faturas fechadas esperando pagamento.",
    "ahead.cash_covers": "O caixa de {caixa} cobre as faturas e ainda deixa {sobra}.",
    "ahead.cash_short": "O caixa de {caixa} não cobre as faturas: faltam {sobra}.",
    "ahead.cash_only": "O caixa nas contas é de {caixa}.",
    "ahead.cash_unknown": (
        "Falta informar o saldo das contas correntes, então o caixa disponível não pode ser "
        "calculado."
    ),
    "ahead.installments": (
        "Fora dessa conta, há {parcelas_futuras} em parcelas de faturas que ainda não abriram."
    ),
    "before.none": "Nada pendente: o mês pode ser conferido como está.",
    "before.some": (
        "{n_itens} {n_itens:pl(item pede,itens pedem)} a sua atenção antes de fechar o mês."
    ),
    "before.item.difference": "A fatura de {mes} do {nome} tem {valor} {sentido}.",
    "before.item.uncategorized": "Há {n} {n:pl(lançamento,lançamentos)} sem categoria em {mes}.",
    "before.item.no_balance": "A conta {nome} está sem saldo informado.",
    "before.item.no_valuation": "O investimento {nome} está sem avaliação.",
    "before.item.stale_valuation": "A avaliação de {nome} tem {dias} dias.",
    "before.item.backup_old": "O último backup foi há {dias} dias.",
    "before.item.backup_none": "Ainda não há nenhum backup.",
}

SIGNAL_WORDS: dict[str, str] = {
    "below": "abaixo",
    "above": "acima",
    "equal": "igual",
    "slight": "passou um pouco da meta",
    "over": "passou da meta",
    "far": "estourou a meta",
    "informed_higher": "a mais no total informado pelo banco",
    "informed_lower": "a menos no total informado pelo banco",
}

# (title, rows); a row is (label template, value template, bold). A row whose placeholders include
# a slot the note does not have is left out. ``average`` and ``statements`` are built by code.
NOTES: dict[str, tuple[str, tuple[tuple[str, str, bool], ...]]] = {
    "balance": (
        "Saldo e poupança",
        (
            ("Despesas brutas − estornos", "{despesas_brutas} − {estornos}", False),
            ("Receitas − despesas", "{receitas} − {despesas}", False),
            ("Saldo", "{saldo}", True),
            ("Saldo ÷ receitas", "{poupanca}", True),
        ),
    ),
    "top": (
        "Maiores categorias",
        (
            ("{categoria_1} + {categoria_2}", "{valor_1} + {valor_2}", False),
            ("{categoria_1}", "{valor_1}", False),
            ("Soma", "{soma_top}", True),
            ("Soma ÷ despesas", "{pct_top}", True),
        ),
    ),
    "recurring": (
        "Recorrentes",
        (
            ("Itens recorrentes", "{n_rec}", False),
            ("Recorrentes", "{recorrentes}", False),
            ("Despesas", "{despesas}", False),
            ("Recorrentes ÷ despesas", "{pct_rec}", True),
        ),
    ),
    "goal": (
        "{categoria}",
        (
            ("{mes:cap} − meta", "{gasto} − {meta}", False),
            ("Acima da meta", "{excesso}", True),
        ),
    ),
    "others": (
        "Outras acima da meta",
        (("{nomes}", "{soma_excessos}", True),),
    ),
    "cash": (
        "Caixa e faturas",
        (
            ("Caixa − faturas", "{caixa} − {faturas_total}", False),
            ("Resultado", "{sobra}", True),
        ),
    ),
    "installments": (
        "Parcelas futuras",
        (("Faturas ainda não abertas, todos os cartões", "{parcelas_futuras}", True),),
    ),
}

TODOS: dict[str, tuple[str, str, str]] = {
    "difference": (
        "Conferir a fatura do {nome}",
        "{valor} de diferença para o total informado pelo banco",
        "Abrir fatura",
    ),
    "uncategorized": (
        "Categorizar {n} {n:pl(lançamento,lançamentos)}",
        "Lançamentos de {mes} em “Não categorizado”",
        "Abrir lançamentos",
    ),
    "no_balance": (
        "Informar o saldo de {nome}",
        "Sem saldo informado; o caixa fica parcial",
        "Informar saldo",
    ),
    "no_valuation": (
        "Avaliar {nome}",
        "Ainda sem avaliação; o patrimônio fica parcial",
        "Avaliar",
    ),
    "stale_valuation": ("Atualizar {nome}", "Sem avaliação há {dias} dias", "Atualizar"),
    "backup_old": ("Fazer um backup", "O último foi há {dias} dias", "Fazer backup"),
    "backup_none": ("Fazer o primeiro backup", "Ainda não há nenhum", "Fazer backup"),
}

NOTICE_OFF = (
    "Nada saiu deste computador: esta carta foi escrita pelo sistema, com um modelo fixo e as "
    "suas contas. Nenhum modelo de linguagem externo foi usado."
)
STATUS_OFF = "Assistente desligado"
HOW_WRITTEN = (
    "Cada frase desta carta é um modelo fixo do sistema, com marcadores como {receitas} e "
    "{despesas}. O sistema troca cada marcador pela conta que fez e mostra a conta ao lado. "
    "Nenhum modelo de linguagem escreveu o texto e nada foi enviado. Quando o assistente "
    "existir, ele só escolherá as palavras: os números continuarão vindo daqui."
)
SENT_NOTE = "À margem"
SENT_HELP = (
    "Cada número do texto tem uma nota ao lado com a conta que o sistema fez. Passe o cursor "
    "sobre um valor para destacá-la."
)

# the kinds that are figures the system computed: they are the markers of the letter
_NUMERIC = frozenset(
    {
        SlotKind.MONEY,
        SlotKind.SIGNED_MONEY,
        SlotKind.PERCENT,
        SlotKind.COUNT,
        SlotKind.DAYS,
        SlotKind.DATE,
    }
)

_PLACEHOLDER = re.compile(r"\{(\w+)(?::(cap|pl\(([^)]*)\)))?\}")


@dataclass(frozen=True)
class TextPart:
    text: str
    kind: ClassVar[str] = "text"


@dataclass(frozen=True)
class ValuePart:
    text: str
    note: int | None  # number of the note that explains it
    slot: str  # the placeholder name: the contract with the assistant
    kind: ClassVar[str] = "value"


@dataclass(frozen=True)
class MarkerPart:
    number: int
    kind: ClassVar[str] = "marker"


type Part = TextPart | ValuePart | MarkerPart


@dataclass(frozen=True)
class RenderedNoteRow:
    label: str
    value: str
    bold: bool


@dataclass(frozen=True)
class RenderedNote:
    number: int
    title: str
    rows: tuple[RenderedNoteRow, ...]


@dataclass(frozen=True)
class RenderedSection:
    id: str
    title: str
    parts: tuple[Part, ...]
    notes: tuple[RenderedNote, ...]


@dataclass(frozen=True)
class RenderedTodo:
    code: str
    title: str
    sub: str
    cta: str
    todo: Todo


@dataclass(frozen=True)
class RenderedLetter:
    sections: tuple[RenderedSection, ...]
    todos: tuple[RenderedTodo, ...]
    slots_total: int  # value markers in the text
    slots_filled: int  # filled by the system (always all of them)
    notes_total: int
    margins: Mapping[str, str] = field(default_factory=lambda: {})


def format_slot(slot: Slot) -> str:
    """How a computed value is shown (pt-BR)."""
    value = slot.value
    match slot.kind:
        case SlotKind.MONEY:
            assert isinstance(value, int)
            return format_brl(value)
        case SlotKind.SIGNED_MONEY:
            assert isinstance(value, int)
            return format_brl(value).replace("-", "−")
        case SlotKind.PERCENT:
            assert isinstance(value, float | int)
            return format_percent(value).replace("-", "−")
        case SlotKind.COUNT | SlotKind.DAYS:
            return str(value)
        case SlotKind.DATE:
            assert hasattr(value, "strftime")
            return value.strftime("%d/%m")  # type: ignore[union-attr]
        case SlotKind.MONTH:
            assert isinstance(value, YearMonth)
            return MONTH_NAMES[value.month - 1]
        case SlotKind.MONTHS:
            assert isinstance(value, tuple)
            names = [MONTH_ABBREVIATIONS[m.month - 1] for m in value if isinstance(m, YearMonth)]
            return _join(names)
        case SlotKind.NAME:
            return str(value)
        case SlotKind.NAMES:
            assert isinstance(value, tuple)
            return _join([str(v) for v in value])
        case SlotKind.SIGNAL:
            return SIGNAL_WORDS.get(str(value), str(value))
    return str(value)  # pragma: no cover


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " e " + items[-1]


def _placeholders(template: str) -> list[str]:
    return [m.group(1) for m in _PLACEHOLDER.finditer(template)]


def _fill(template: str, slots: Mapping[str, Slot]) -> str:
    """The plain text of a template (used for notes and to-dos, where nothing is a value part)."""

    def replace(match: re.Match[str]) -> str:
        name, spec, words = match.group(1), match.group(2), match.group(3)
        slot = slots[name]
        if spec and spec.startswith("pl"):
            one, many = (words or ",").split(",", 1)
            return one if slot.value == 1 else many
        text = format_slot(slot)
        return text[:1].upper() + text[1:] if spec == "cap" else text

    return _PLACEHOLDER.sub(replace, template)


def _parts(template: str, slots: Mapping[str, Slot], note: int | None) -> list[Part]:
    parts: list[Part] = []
    position = 0
    for match in _PLACEHOLDER.finditer(template):
        if match.start() > position:
            parts.append(TextPart(template[position : match.start()]))
        name, spec, words = match.group(1), match.group(2), match.group(3)
        slot = slots[name]
        if spec and spec.startswith("pl"):
            one, many = (words or ",").split(",", 1)
            parts.append(TextPart(one if slot.value == 1 else many))
        else:
            text = format_slot(slot)
            if spec == "cap":
                text = text[:1].upper() + text[1:]
            if slot.kind not in _NUMERIC:
                parts.append(TextPart(text))  # words, names and months are text, not figures
            else:
                parts.append(ValuePart(text, note, name))
        position = match.end()
    if position < len(template):
        parts.append(TextPart(template[position:]))
    return parts


def _note_rows(key: str, slots: Mapping[str, Slot]) -> tuple[str, list[RenderedNoteRow]]:
    if key == "average":
        count = int(slots["n"].value) if isinstance(slots["n"].value, int) else 0
        values = [slots[f"m_{i}"] for i in range(1, count + 1) if f"m_{i}" in slots]
        months = slots["meses"]
        title = f"Média de {format_slot(months)}"
        rows = [
            RenderedNoteRow(
                " + ".join(format_slot(v) for v in values) if values else "Meses com lançamentos",
                f"÷ {count}",
                False,
            ),
            RenderedNoteRow(
                "Média menos o mês",
                f"{format_slot(slots['media'])} − {format_slot(slots['despesas'])} = "
                f"{format_slot(slots['diferenca'])}",
                True,
            ),
        ]
        return title, rows
    if key == "statements":
        listed = [i for i in range(1, 4) if f"nome_{i}" in slots]
        label = " + ".join(
            f"{format_slot(slots[f'nome_{i}'])} {format_slot(slots[f'vence_{i}'])}" for i in listed
        )
        value = " + ".join(format_slot(slots[f"valor_{i}"]) for i in listed)
        rows = [
            RenderedNoteRow(label, value, False),
            RenderedNoteRow("Total", format_slot(slots["total"]), True),
        ]
        count = slots["n"].value
        if isinstance(count, int) and len(listed) < count:
            rows.insert(1, RenderedNoteRow("Demais faturas", "…", False))
        return "Faturas fechadas", rows
    title_template, templates = NOTES[key]
    rows = [
        RenderedNoteRow(_fill(label, slots), _fill(value, slots), bold)
        for label, value, bold in templates
        if all(name in slots for name in _placeholders(label) + _placeholders(value))
    ]
    return _fill(title_template, slots), rows


def render_sentence(sentence: Sentence, number: int | None) -> list[Part]:
    parts = _parts(SENTENCES[sentence.code], sentence.slots, number)
    if number is not None:
        parts.append(MarkerPart(number))
    return parts


def render_section(section: Section, first_note: int) -> tuple[RenderedSection, int]:
    parts: list[Part] = []
    notes: list[RenderedNote] = []
    number = first_note
    for sentence in section.sentences:
        note: Note | None = sentence.note
        if parts:
            parts.append(TextPart(" "))
        if note is None:
            parts.extend(render_sentence(sentence, None))
            continue
        parts.extend(render_sentence(sentence, number))
        title, rows = _note_rows(note.key, note.slots)
        notes.append(RenderedNote(number, title, tuple(rows)))
        number += 1
    return (
        RenderedSection(section.id, SECTION_TITLES[section.id], tuple(parts), tuple(notes)),
        number,
    )


def render_todo(todo: Todo) -> RenderedTodo:
    title, sub, cta = TODOS[todo.code]
    return RenderedTodo(todo.code, _fill(title, todo.slots), _fill(sub, todo.slots), cta, todo)


def render_letter(letter: Letter) -> RenderedLetter:
    """Fill every template with the computed slots; count the markers for the transparency box."""
    sections: list[RenderedSection] = []
    number = 1
    for section in letter.sections:
        rendered, number = render_section(section, number)
        sections.append(rendered)
    total = sum(1 for s in sections for p in s.parts if isinstance(p, ValuePart))
    return RenderedLetter(
        tuple(sections),
        tuple(render_todo(t) for t in letter.todos),
        slots_total=total,
        slots_filled=total,
        notes_total=number - 1,
    )
