"""Settings — re-exported from app/config.py (F-OPS-03).

This module used to hold a second Settings class. Two classes reading the
same environment drifted: different defaults for DATABASE_URL and the Celery
URLs, two copies of the ENCRYPTION_KEY rules, and no single place to look.
Its fields now live, unchanged, in app/config.py, and the names it shared
with that class are read-only aliases there (settings.REDIS_URL reads
settings.redis_url). Importing from here keeps working; new code imports
app.config.
"""
from __future__ import annotations

from app.config import Settings, get_settings, settings

__all__ = ["Settings", "get_settings", "settings"]
