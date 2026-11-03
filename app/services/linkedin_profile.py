"""LinkedIn profile URLs: parse and normalise. No network, no provider.

The one place the slug rule lives, so suppression (lead_tasks.is_suppressed,
consent.suppress_everywhere), L0 tasks and the connections import all compare
the same key. linkedin_outreach / linkedin_posts re-export these for older
callers.
"""

from __future__ import annotations

import re

_SLUG = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
PUBLIC_PROFILE_PREFIX = "https://www.linkedin.com/in/"


def public_identifier(linkedin_url: str | None) -> str | None:
    """'https://www.linkedin.com/in/sara-khan-12ab/' -> 'sara-khan-12ab'."""
    match = _SLUG.search(linkedin_url or "")
    return match.group(1).strip() if match else None


def normalize_profile(url_or_slug: str | None) -> str | None:
    """The suppression/task key: the slug, lower-cased; a bare slug passes."""
    if not url_or_slug:
        return None
    slug = public_identifier(url_or_slug) or url_or_slug
    return slug.strip().strip("/").lower()[:200] or None


def public_profile_url(url_or_slug: str | None) -> str | None:
    """A plain https://www.linkedin.com/in/<slug>/ link for the user to open,
    or None when the input is not a member profile. Only /in/ profiles: a
    company page, a post or any other LinkedIn URL is not a person."""
    slug = public_identifier(url_or_slug)
    if not slug or not re.fullmatch(r"[\w\-%.]{2,100}", slug):
        return None
    return f"{PUBLIC_PROFILE_PREFIX}{slug}/"
