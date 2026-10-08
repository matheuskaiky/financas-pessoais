import datetime as dt

import pytest

from fakes import MemoryImageStore, MemoryUnitOfWork
from financas.application.use_cases.catalog import (
    AppearanceTarget,
    CreateAccount,
    CreateAccountCommand,
    CreateCategory,
    CreateCategoryCommand,
    CreateInstitution,
    CreateInstitutionCommand,
    RenameCategory,
    SetAccountActive,
    SetAppearance,
    SetAppearanceCommand,
)
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    CategoryGroup,
    CategoryKind,
    Institution,
)
from financas.infrastructure.db.seed import INITIAL_CATEGORIES, NEUTRAL_CATEGORIES, seed_categories

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def code(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


def test_seed_creates_every_initial_category_once(uow: MemoryUnitOfWork) -> None:
    assert len(uow.categories.list_all()) == len(INITIAL_CATEGORIES) + len(NEUTRAL_CATEGORIES)
    assert seed_categories(uow) == 0
    assert uow.categories.get_by_slug("groceries") is not None
    assert uow.categories.get_by_slug("food") is not None


def test_food_is_non_essential_and_groceries_essential(uow: MemoryUnitOfWork) -> None:
    groceries = uow.categories.get_by_slug("groceries")
    food = uow.categories.get_by_slug("food")
    assert groceries and groceries.group is CategoryGroup.ESSENTIAL
    assert food and food.group is CategoryGroup.NON_ESSENTIAL


def test_institution_slug_comes_from_the_name_without_accents(uow: MemoryUnitOfWork) -> None:
    institution = CreateInstitution(uow).execute(
        CreateInstitutionCommand(name="  Caixa Econômica ")
    )
    assert institution.slug == "caixa_economica"
    assert institution.name == "Caixa Econômica"


def test_institution_duplicate_slug_is_rejected(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    with pytest.raises(DomainError) as exc:
        CreateInstitution(uow).execute(CreateInstitutionCommand(name="banco do brasil"))
    assert code(exc) == "DUPLICATE_SLUG"
    assert len(uow.institutions.list_all()) == 1


@pytest.mark.parametrize("name", ["", "   ", "???"])
def test_institution_needs_a_usable_name(uow: MemoryUnitOfWork, name: str) -> None:
    with pytest.raises(DomainError) as exc:
        CreateInstitution(uow).execute(CreateInstitutionCommand(name=name))
    assert code(exc) in {"EMPTY_NAME", "INVALID_SLUG"}


def test_institution_color_is_validated_and_normalized(uow: MemoryUnitOfWork) -> None:
    ok = CreateInstitution(uow).execute(CreateInstitutionCommand(name="Inter", color="#ff7a00"))
    assert ok.color == "#FF7A00"
    with pytest.raises(DomainError) as exc:
        CreateInstitution(uow).execute(CreateInstitutionCommand(name="Nu", color="roxo"))
    assert code(exc) == "INVALID_COLOR"


def test_account_requires_an_existing_institution(uow: MemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as exc:
        CreateAccount(uow).execute(CreateAccountCommand(AccountKind.CHECKING, "nope", "Conta"))
    assert code(exc) == "NOT_FOUND"


def test_opening_balance_becomes_the_first_informed_balance(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    on = dt.date(2026, 1, 1)
    account = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.CHECKING,
            institution.id,
            "Conta",
            opening_balance_cents=30_421,
            opening_balance_on=on,
        )
    )
    (anchor,) = uow.anchors.list_for_account(account.id)
    assert (anchor.on_date, anchor.balance_cents, anchor.gross_balance_cents) == (on, 30_421, None)
    # an overdrawn opening balance is allowed (signed)
    other = CreateAccount(uow).execute(
        CreateAccountCommand(
            AccountKind.CHECKING,
            institution.id,
            "Outra",
            opening_balance_cents=-500,
            opening_balance_on=on,
        )
    )
    assert uow.anchors.list_for_account(other.id)[0].balance_cents == -500


def test_opening_balance_needs_both_value_and_date_and_not_for_cards(
    uow: MemoryUnitOfWork, institution: Institution
) -> None:
    for kwargs in ({"opening_balance_cents": 100}, {"opening_balance_on": dt.date(2026, 1, 1)}):
        with pytest.raises(DomainError) as exc:
            CreateAccount(uow).execute(
                CreateAccountCommand(AccountKind.CHECKING, institution.id, "Conta", **kwargs)  # type: ignore[arg-type]
            )
        assert code(exc) == "OPENING_BALANCE_INCOMPLETE"
    with pytest.raises(DomainError) as exc:
        CreateAccount(uow).execute(
            CreateAccountCommand(
                AccountKind.CREDIT_CARD,
                institution.id,
                "Cartão",
                closing_days_before_due=11,
                due_day=5,
                opening_balance_cents=100,
                opening_balance_on=dt.date(2026, 1, 1),
            )
        )
    assert code(exc) == "BALANCE_NOT_FOR_CARDS"
    assert uow.accounts.list_all() == []  # nothing was half-created


def test_account_can_be_deactivated(uow: MemoryUnitOfWork, checking: Account) -> None:
    updated = SetAccountActive(uow).execute(checking.id, False)
    assert updated.is_active is False
    assert uow.accounts.get(checking.id) == updated


def test_category_group_must_match_kind(uow: MemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as exc:
        CreateCategory(uow).execute(
            CreateCategoryCommand("Pets", CategoryGroup.ESSENTIAL, CategoryKind.INCOME)
        )
    assert code(exc) == "CATEGORY_GROUP_KIND_MISMATCH"


def test_category_duplicate_slug_and_negative_budget(uow: MemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as exc:
        CreateCategory(uow).execute(
            CreateCategoryCommand(
                "Mercado", CategoryGroup.ESSENTIAL, CategoryKind.EXPENSE, slug="groceries"
            )
        )
    assert code(exc) == "DUPLICATE_SLUG"
    with pytest.raises(DomainError) as exc:
        CreateCategory(uow).execute(
            CreateCategoryCommand(
                "Pets", CategoryGroup.NON_ESSENTIAL, CategoryKind.EXPENSE, monthly_budget_cents=-1
            )
        )
    assert code(exc) == "AMOUNT_NOT_POSITIVE"


def test_set_appearance_sets_color_and_image(
    uow: MemoryUnitOfWork, images: MemoryImageStore, institution: Institution
) -> None:
    SetAppearance(uow, images).execute(
        SetAppearanceCommand(
            AppearanceTarget.INSTITUTION, institution.id, color="#1e395f", image=PNG
        )
    )
    stored = uow.institutions.get(institution.id)
    assert stored and stored.color == "#1E395F" and stored.image_id in images.files


@pytest.mark.parametrize(("typed", "stored"), [("#FF7A00", "#FF7A00"), ("#820ad1", "#820AD1")])
def test_account_color_is_persisted_and_normalized(
    uow: MemoryUnitOfWork, images: MemoryImageStore, checking: Account, typed: str, stored: str
) -> None:
    SetAppearance(uow, images).execute(
        SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, color=typed)
    )
    saved = uow.accounts.get(checking.id)
    assert saved and saved.color == stored


@pytest.mark.parametrize("bad", ["roxo", "#12345", "#GGGGGG", "FF7A00", "#FF7A001"])
def test_an_invalid_account_color_is_rejected_and_changes_nothing(
    uow: MemoryUnitOfWork, images: MemoryImageStore, checking: Account, bad: str
) -> None:
    with pytest.raises(DomainError) as exc:
        SetAppearance(uow, images).execute(
            SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, color=bad)
        )
    assert code(exc) == "INVALID_COLOR"
    assert (saved := uow.accounts.get(checking.id)) and saved.color == checking.color


def test_an_empty_color_clears_the_accounts_own_color(
    uow: MemoryUnitOfWork, images: MemoryImageStore, checking: Account
) -> None:
    use_case = SetAppearance(uow, images)
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, color="#FF7A00"))
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, color=""))
    assert (saved := uow.accounts.get(checking.id)) and saved.color is None


def test_replacing_or_removing_an_image_deletes_the_old_file(
    uow: MemoryUnitOfWork, images: MemoryImageStore, checking: Account
) -> None:
    use_case = SetAppearance(uow, images)
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, image=PNG))
    first = uow.accounts.get(checking.id)
    assert first and first.image_id
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, image=PNG))
    second = uow.accounts.get(checking.id)
    assert second and second.image_id and second.image_id != first.image_id
    assert set(images.files) == {second.image_id}
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, remove_image=True))
    assert images.files == {}
    assert (stored := uow.accounts.get(checking.id)) and stored.image_id is None


def test_color_only_update_keeps_the_image(
    uow: MemoryUnitOfWork, images: MemoryImageStore, checking: Account
) -> None:
    use_case = SetAppearance(uow, images)
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, image=PNG))
    use_case.execute(SetAppearanceCommand(AppearanceTarget.ACCOUNT, checking.id, color="#0E6151"))
    stored = uow.accounts.get(checking.id)
    assert stored and stored.color == "#0E6151" and stored.image_id in images.files


def test_invalid_image_is_rejected_and_nothing_changes(
    uow: MemoryUnitOfWork, images: MemoryImageStore, institution: Institution
) -> None:
    with pytest.raises(DomainError) as exc:
        SetAppearance(uow, images).execute(
            SetAppearanceCommand(
                AppearanceTarget.INSTITUTION, institution.id, color="#112233", image=b"<svg/>"
            )
        )
    assert code(exc) == "IMAGE_TYPE_NOT_ALLOWED"
    stored = uow.institutions.get(institution.id)
    assert stored and stored.color is None and stored.image_id is None


def test_categories_have_color_but_no_image(
    uow: MemoryUnitOfWork, images: MemoryImageStore
) -> None:
    category = uow.categories.get_by_slug("food")
    assert category
    use_case = SetAppearance(uow, images)
    use_case.execute(SetAppearanceCommand(AppearanceTarget.CATEGORY, category.id, color="#aa0000"))
    updated = uow.categories.get(category.id)
    assert updated and updated.color == "#AA0000"
    with pytest.raises(DomainError) as exc:
        use_case.execute(SetAppearanceCommand(AppearanceTarget.CATEGORY, category.id, image=PNG))
    assert code(exc) == "IMAGE_NOT_SUPPORTED"


def test_rename_category_keeps_the_slug(uow: MemoryUnitOfWork) -> None:
    food = uow.categories.get_by_slug("food")
    assert food
    renamed = RenameCategory(uow).execute(food.id, "  Restaurantes e Açaí ")
    assert renamed.name == "Restaurantes e Açaí" and renamed.slug == "food"
    assert uow.categories.get(food.id) == renamed


@pytest.mark.parametrize("name", ["", "   "])
def test_rename_category_rejects_empty_name(uow: MemoryUnitOfWork, name: str) -> None:
    food = uow.categories.get_by_slug("food")
    assert food
    with pytest.raises(DomainError) as exc:
        RenameCategory(uow).execute(food.id, name)
    assert exc.value.code == "EMPTY_NAME"


def test_rename_category_name_is_unique_ignoring_case_and_accents(uow: MemoryUnitOfWork) -> None:
    food = uow.categories.get_by_slug("food")
    health = uow.categories.get_by_slug("health")
    assert food and health
    with pytest.raises(DomainError) as exc:
        RenameCategory(uow).execute(food.id, "SAUDE")  # "Saúde" exists
    assert exc.value.code == "DUPLICATE_NAME"
    assert uow.categories.get(food.id) == food
    # keeping its own name (even with another case) is not a conflict
    assert RenameCategory(uow).execute(health.id, "saúde").name == "saúde"


def test_rename_unknown_category(uow: MemoryUnitOfWork) -> None:
    with pytest.raises(DomainError) as exc:
        RenameCategory(uow).execute("nope", "Algo")
    assert exc.value.code == "NOT_FOUND"


# --- neutral (pass-through) categories ---


def test_the_pass_through_category_is_seeded_neutral(uow: MemoryUnitOfWork) -> None:
    third = uow.categories.get_by_slug("third_party")
    assert third and third.is_neutral and third.name == "Reembolso / Terceiros"
    assert not uow.categories.get_by_slug("food").is_neutral  # type: ignore[union-attr]


def test_a_neutral_category_takes_both_expenses_and_incomes(
    uow: MemoryUnitOfWork, checking: Account
) -> None:
    from financas.application.use_cases.transactions import (
        RegisterTransaction,
        RegisterTransactionCommand,
    )
    from financas.domain.models import TransactionKind

    third = uow.categories.get_by_slug("third_party")
    assert third
    for kind in (TransactionKind.EXPENSE, TransactionKind.INCOME):
        entry = RegisterTransaction(uow).execute(
            RegisterTransactionCommand(
                checking.id, dt.date(2026, 7, 10), kind, 25_000, "luz do pai", category_id=third.id
            )
        )
        assert entry.category_id == third.id
    food = uow.categories.get_by_slug("food")
    assert food
    with pytest.raises(DomainError) as exc:  # an ordinary expense category still refuses income
        RegisterTransaction(uow).execute(
            RegisterTransactionCommand(
                checking.id,
                dt.date(2026, 7, 10),
                TransactionKind.INCOME,
                1_000,
                "x",
                category_id=food.id,
            )
        )
    assert code(exc) == "CATEGORY_KIND_MISMATCH"


def test_a_category_can_be_marked_and_unmarked_neutral(uow: MemoryUnitOfWork) -> None:
    from financas.application.use_cases.catalog import SetCategoryNeutral

    food = uow.categories.get_by_slug("food")
    assert food and not food.is_neutral
    marked = SetCategoryNeutral(uow).execute(food.id, True)
    assert marked.is_neutral and uow.categories.get(food.id).is_neutral  # type: ignore[union-attr]
    assert not SetCategoryNeutral(uow).execute(food.id, False).is_neutral


def test_a_movement_category_cannot_be_neutral(uow: MemoryUnitOfWork) -> None:
    from financas.application.use_cases.catalog import SetCategoryNeutral

    transfer = uow.categories.get_by_slug("transfer")
    assert transfer
    with pytest.raises(DomainError) as exc:
        SetCategoryNeutral(uow).execute(transfer.id, True)
    assert code(exc) == "NEUTRAL_NEEDS_EXPENSE_OR_INCOME"
