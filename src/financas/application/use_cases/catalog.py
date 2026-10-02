"""Institutions, accounts, categories and their appearance (9.9)."""

from dataclasses import dataclass, replace
from enum import StrEnum

from financas.application.use_cases._common import found, new_id
from financas.domain.errors import DomainError
from financas.domain.models import (
    Account,
    AccountKind,
    AssetClass,
    Category,
    CategoryGroup,
    CategoryKind,
    Institution,
    InvestmentTracking,
)
from financas.domain.ports import ImageStore, UnitOfWork
from financas.domain.rules import normalize_color, validate_card_settings, validate_group_kind
from financas.domain.services.text import clean_text, slugify


def _name(text: str) -> str:
    name = clean_text(text)
    if not name:
        raise DomainError("EMPTY_NAME")
    return name


def _slug(explicit: str | None, name: str) -> str:
    slug = slugify(explicit if explicit else name)
    if not slug:
        raise DomainError("INVALID_SLUG")
    return slug


@dataclass(frozen=True)
class CreateInstitutionCommand:
    name: str
    slug: str | None = None
    group_slug: str | None = None
    color: str | None = None


class CreateInstitution:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CreateInstitutionCommand) -> Institution:
        name = _name(cmd.name)
        institution = Institution(
            id=new_id(),
            slug=_slug(cmd.slug, name),
            name=name,
            group_slug=slugify(cmd.group_slug) if cmd.group_slug else None,
            color=normalize_color(cmd.color),
        )
        with self._uow as uow:
            if uow.institutions.get_by_slug(institution.slug) is not None:
                raise DomainError("DUPLICATE_SLUG", slug=institution.slug)
            uow.institutions.add(institution)
            uow.commit()
        return institution


@dataclass(frozen=True)
class CreateAccountCommand:
    kind: AccountKind
    institution_id: str
    nickname: str
    color: str | None = None
    closing_days_before_due: int | None = None  # credit cards only
    due_day: int | None = None
    credit_limit_cents: int | None = None
    asset_class: AssetClass | None = None  # investment accounts only (default: other)
    is_emergency_fund: bool = False


class CreateAccount:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CreateAccountCommand) -> Account:
        if cmd.kind is AccountKind.CREDIT_CARD:
            validate_card_settings(cmd.closing_days_before_due, cmd.due_day, cmd.credit_limit_cents)
        elif (cmd.closing_days_before_due, cmd.due_day, cmd.credit_limit_cents) != (
            None,
            None,
            None,
        ):
            raise DomainError("CARD_FIELDS_ONLY_FOR_CARDS")
        investment = cmd.kind is AccountKind.INVESTMENT
        if not investment and (cmd.asset_class is not None or cmd.is_emergency_fund):
            raise DomainError("INVESTMENT_FIELDS_ONLY_FOR_INVESTMENTS")
        account = Account(
            id=new_id(),
            kind=cmd.kind,
            institution_id=cmd.institution_id,
            nickname=_name(cmd.nickname),
            color=normalize_color(cmd.color),
            closing_days_before_due=cmd.closing_days_before_due,
            due_day=cmd.due_day,
            credit_limit_cents=cmd.credit_limit_cents,
            tracking=InvestmentTracking.ACCOUNT if investment else None,
            asset_class=(cmd.asset_class or AssetClass.OTHER) if investment else None,
            is_emergency_fund=cmd.is_emergency_fund,
        )
        with self._uow as uow:
            found(uow.institutions.get(cmd.institution_id), "institution")
            uow.accounts.add(account)
            uow.commit()
        return account


class SetInvestmentSettings:
    """Asset class and emergency-fund flag of an investment account (9.6)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        account_id: str,
        asset_class: AssetClass,
        is_emergency_fund: bool,
        tracking: InvestmentTracking | None = None,
    ) -> Account:
        """``tracking`` switches the level; refused while the level in use holds data (9.6)."""
        with self._uow as uow:
            account = found(uow.accounts.get(account_id), "account")
            if account.kind is not AccountKind.INVESTMENT:
                raise DomainError("INVESTMENT_REQUIRED")
            new_tracking = tracking or account.tracking
            if new_tracking is not account.tracking:
                in_use = (
                    uow.anchors.list_for_account(account.id)
                    if account.tracking is InvestmentTracking.ACCOUNT
                    else uow.holdings.list_for_account(account.id)
                )
                if in_use or uow.transactions.movements(account.id):
                    raise DomainError("TRACKING_IN_USE")
            account = replace(
                account,
                asset_class=asset_class,
                is_emergency_fund=is_emergency_fund,
                tracking=new_tracking,
            )
            uow.accounts.update(account)
            uow.commit()
        return account


class SetAccountActive:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, account_id: str, is_active: bool) -> Account:
        with self._uow as uow:
            account = replace(found(uow.accounts.get(account_id), "account"), is_active=is_active)
            uow.accounts.update(account)
            uow.commit()
        return account


@dataclass(frozen=True)
class CreateCategoryCommand:
    name: str
    group: CategoryGroup
    kind: CategoryKind
    slug: str | None = None
    monthly_budget_cents: int | None = None
    color: str | None = None


class CreateCategory:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(self, cmd: CreateCategoryCommand) -> Category:
        validate_group_kind(cmd.group, cmd.kind)
        if cmd.monthly_budget_cents is not None and cmd.monthly_budget_cents <= 0:
            raise DomainError("AMOUNT_NOT_POSITIVE")
        name = _name(cmd.name)
        category = Category(
            id=new_id(),
            slug=_slug(cmd.slug, name),
            name=name,
            group=cmd.group,
            kind=cmd.kind,
            monthly_budget_cents=cmd.monthly_budget_cents,
            color=normalize_color(cmd.color),
        )
        with self._uow as uow:
            if uow.categories.get_by_slug(category.slug) is not None:
                raise DomainError("DUPLICATE_SLUG", slug=category.slug)
            uow.categories.add(category)
            uow.commit()
        return category


class AppearanceTarget(StrEnum):
    INSTITUTION = "institution"
    ACCOUNT = "account"
    CATEGORY = "category"


@dataclass(frozen=True)
class SetAppearanceCommand:
    """The full desired look: ``color=None`` clears the color; ``image`` replaces the image."""

    target: AppearanceTarget
    entity_id: str
    color: str | None = None
    image: bytes | None = None
    remove_image: bool = False


class SetAppearance:
    def __init__(self, uow: UnitOfWork, images: ImageStore) -> None:
        self._uow = uow
        self._images = images

    def execute(self, cmd: SetAppearanceCommand) -> None:
        color = normalize_color(cmd.color)
        if cmd.target is AppearanceTarget.CATEGORY and (cmd.image or cmd.remove_image):
            raise DomainError("IMAGE_NOT_SUPPORTED", target=cmd.target.value)
        with self._uow as uow:
            old_image_id, new_image_id = self._load_and_update(uow, cmd, color)
            uow.commit()
        if old_image_id and old_image_id != new_image_id:
            self._images.delete(old_image_id)

    def _load_and_update(
        self, uow: UnitOfWork, cmd: SetAppearanceCommand, color: str | None
    ) -> tuple[str | None, str | None]:
        match cmd.target:
            case AppearanceTarget.CATEGORY:
                category = found(uow.categories.get(cmd.entity_id), "category")
                uow.categories.update(replace(category, color=color))
                return None, None
            case AppearanceTarget.INSTITUTION:
                institution = found(uow.institutions.get(cmd.entity_id), "institution")
                image_id = self._next_image_id(institution.image_id, cmd)
                uow.institutions.update(replace(institution, color=color, image_id=image_id))
                return institution.image_id, image_id
            case AppearanceTarget.ACCOUNT:
                account = found(uow.accounts.get(cmd.entity_id), "account")
                image_id = self._next_image_id(account.image_id, cmd)
                uow.accounts.update(replace(account, color=color, image_id=image_id))
                return account.image_id, image_id

    def _next_image_id(self, current: str | None, cmd: SetAppearanceCommand) -> str | None:
        if cmd.image is not None:
            return self._images.save(cmd.image)
        return None if cmd.remove_image else current
