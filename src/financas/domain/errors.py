"""Domain errors: stable codes with parameters. The UI turns them into pt-BR sentences."""


class DomainError(Exception):
    """A business rule was violated. ``code`` is ASCII and stable; never a display text."""

    def __init__(self, code: str, **params: str | int) -> None:
        super().__init__(code)
        self.code = code
        self.params: dict[str, str | int] = params

    def __repr__(self) -> str:
        return f"DomainError({self.code!r}, {self.params!r})"
