"""Initial categories (CLAUDE.md 9.2). Slugs are stable; names are pt-BR *data*, user-editable."""

from financas.application.use_cases.catalog import CreateCategory, CreateCategoryCommand
from financas.domain.models import CategoryGroup, CategoryKind
from financas.domain.ports import UnitOfWork

G = CategoryGroup
K = CategoryKind

# (slug, name, group, kind)
INITIAL_CATEGORIES: tuple[tuple[str, str, CategoryGroup, CategoryKind], ...] = (
    ("groceries", "Supermercado", G.ESSENTIAL, K.EXPENSE),
    ("health", "Saúde", G.ESSENTIAL, K.EXPENSE),
    ("transport", "Transporte", G.ESSENTIAL, K.EXPENSE),
    ("telecom", "Telecom", G.ESSENTIAL, K.EXPENSE),
    ("insurance", "Seguros", G.ESSENTIAL, K.EXPENSE),
    ("home", "Casa", G.ESSENTIAL, K.EXPENSE),
    ("education", "Educação", G.ESSENTIAL, K.EXPENSE),
    ("food", "Alimentação", G.NON_ESSENTIAL, K.EXPENSE),
    ("shopping", "Compras", G.NON_ESSENTIAL, K.EXPENSE),
    ("services", "Serviços", G.NON_ESSENTIAL, K.EXPENSE),
    ("subscriptions", "Assinaturas", G.NON_ESSENTIAL, K.EXPENSE),
    ("other", "Outros", G.NON_ESSENTIAL, K.EXPENSE),
    ("fees", "Encargos", G.CHARGES, K.EXPENSE),
    ("taxes", "Impostos", G.CHARGES, K.EXPENSE),
    ("salary", "Salário", G.INCOME, K.INCOME),
    ("investment_income", "Rendimentos", G.INCOME, K.INCOME),
    ("other_income", "Outras receitas", G.INCOME, K.INCOME),
    ("transfer", "Transferência", G.MOVEMENT, K.NEUTRAL),
    ("refund", "Estorno", G.MOVEMENT, K.NEUTRAL),
    ("uncategorized", "Não categorizado", G.REVIEW, K.EXPENSE),
)


# pass-through categories (slug, name, group, kind): money that crosses the account for someone else
# and must not count as the user's own spending or income (CLAUDE.md 9.14)
NEUTRAL_CATEGORIES: tuple[tuple[str, str, CategoryGroup, CategoryKind], ...] = (
    ("third_party", "Reembolso / Terceiros", G.NON_ESSENTIAL, K.EXPENSE),
)


def seed_categories(uow: UnitOfWork) -> int:
    """Create the initial categories that do not exist yet; return how many were created."""
    with uow as work:
        existing = {c.slug for c in work.categories.list_all()}
    created = 0
    for slug, name, group, kind in INITIAL_CATEGORIES:
        if slug in existing:
            continue
        CreateCategory(uow).execute(
            CreateCategoryCommand(name=name, slug=slug, group=group, kind=kind)
        )
        created += 1
    for slug, name, group, kind in NEUTRAL_CATEGORIES:
        if slug in existing:
            continue
        CreateCategory(uow).execute(
            CreateCategoryCommand(name=name, slug=slug, group=group, kind=kind, is_neutral=True)
        )
        created += 1
    return created
