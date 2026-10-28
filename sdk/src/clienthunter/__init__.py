"""Vectorae Python SDK.

Quickstart::

    from clienthunter import ClientHunter

    ch = ClientHunter(api_url="https://api.clienthunter.ai")
    ch.auth.login("you@example.com", "secret")  # stores tokens in ~/.clienthunter/config.toml

    product = ch.products.create(
        name="My SaaS",
        description="Project management for remote teams",
        type="product",
        user_email="you@example.com",
    )
    strategy = ch.strategies.create(product_id=product.id)
    print(strategy.status)   # "pending" → pipeline kicks off in the cloud

NOTE: The SDK talks to a running backend (cloud-hosted or self-hosted via
``clienthunter serve``).  Installing this package alone does NOT run campaigns.
"""

from clienthunter._version import __version__
from clienthunter.client import ClientHunter
from clienthunter.exceptions import (
    APIError,
    AuthError,
    ClientHunterError,
    ComplianceError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

__all__ = [
    "__version__",
    "ClientHunter",
    # exceptions
    "ClientHunterError",
    "AuthError",
    "NotFoundError",
    "ValidationError",
    "ComplianceError",
    "RateLimitError",
    "APIError",
]
