"""Storage-layer errors. Messages name the file and row, so data problems are easy to fix."""
from app.domain.models import DomainError


class DataLoadError(DomainError):
    """A source or sandbox file is missing, is not JSON, or has a row that fails validation."""


class SandboxConflictError(DomainError):
    """A sandbox write would duplicate an id, or an action id does not exist."""
