"""Read-only student homework retrieval; no credentials or data are persisted."""

from .client import (
    APIError,
    AuthenticationError,
    ClassChartsError,
    ConfigurationError,
    Homework,
    NetworkError,
    StudentClient,
)

__all__ = [
    "APIError", "AuthenticationError", "ClassChartsError", "ConfigurationError",
    "Homework", "NetworkError", "StudentClient",
]
