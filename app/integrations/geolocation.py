"""IP geolocation — "which country is this request coming from?"

Used once, at onboarding, to compare the country a user DECLARES with the
country their connection appears to be in. The answer is a risk signal only:
a mismatch is flagged for an admin, never a refusal, because VPNs, corporate
egress and travel all produce honest mismatches.

Provider is configuration (GEOLOCATION_PROVIDER), so swapping services is an
env var, not a code change:

  ipapi_co  https://ipapi.co/<ip>/json/        no key; free tier ~1k/day
  ipinfo    https://ipinfo.io/<ip>/json?token= GEOLOCATION_API_KEY
  disabled  never looks anything up

`lookup_country()` NEVER raises. "We could not tell" is a legitimate answer
(None), and onboarding must not fail because a free geolocation API is having
a bad day.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class GeolocationError(Exception):
    """The provider could not answer (HTTP error, quota, malformed body)."""


@dataclass
class GeoResult:
    ip: str
    country_code: str | None
    provider: str
    raw: dict = field(default_factory=dict)


def _http() -> httpx.Client:
    """Factory tests monkeypatch."""
    return httpx.Client(timeout=settings.geolocation_timeout_seconds)


def is_public_ip(value: str | None) -> bool:
    """True only for a routable address worth asking a provider about.

    Loopback, RFC 1918, link-local, the documentation ranges and the
    TestClient's literal "testclient" host are all "not public": a provider
    cannot place them, and asking would spend quota to learn nothing.
    """
    try:
        address = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved
                or address.is_unspecified)


class IpapiCoProvider:
    name = "ipapi_co"
    # TODO: verify against current ipapi.co docs (field names, error shape)
    URL = "https://ipapi.co/{ip}/json/"

    def lookup(self, ip: str) -> GeoResult:
        with _http() as client:
            resp = client.get(self.URL.format(ip=ip),
                              headers={"User-Agent": "Vectorae/1.0"})
        if resp.status_code >= 400:
            raise GeolocationError(f"ipapi.co HTTP {resp.status_code}")
        data = resp.json() or {}
        if data.get("error"):
            raise GeolocationError(f"ipapi.co: {data.get('reason') or 'error'}")
        code = (data.get("country_code") or data.get("country") or "").upper() or None
        return GeoResult(ip=ip, country_code=code, provider=self.name, raw=data)


class IpinfoProvider:
    name = "ipinfo"
    # TODO: verify against current ipinfo.io docs (token param, `country` field)
    URL = "https://ipinfo.io/{ip}/json"

    def lookup(self, ip: str) -> GeoResult:
        params = {"token": settings.geolocation_api_key} if settings.geolocation_api_key else {}
        with _http() as client:
            resp = client.get(self.URL.format(ip=ip), params=params)
        if resp.status_code >= 400:
            raise GeolocationError(f"ipinfo HTTP {resp.status_code}")
        data = resp.json() or {}
        if data.get("bogon"):
            return GeoResult(ip=ip, country_code=None, provider=self.name, raw=data)
        code = (data.get("country") or "").upper() or None
        return GeoResult(ip=ip, country_code=code, provider=self.name, raw=data)


class DisabledProvider:
    name = "disabled"

    def lookup(self, ip: str) -> GeoResult:
        return GeoResult(ip=ip, country_code=None, provider=self.name)


PROVIDERS = {cls.name: cls for cls in (IpapiCoProvider, IpinfoProvider, DisabledProvider)}


def get_provider(name: str | None = None):
    key = (name or settings.geolocation_provider or "disabled").strip().lower()
    cls = PROVIDERS.get(key)
    if cls is None:
        raise GeolocationError(f"unknown GEOLOCATION_PROVIDER {key!r}")
    return cls()


def lookup_country(ip: str | None) -> str | None:
    """ISO alpha-2 country for a public IP, or None when it cannot be told."""
    if not is_public_ip(ip):
        return None
    try:
        result = get_provider().lookup(ip)
    except Exception as exc:  # noqa: BLE001 -- see the module docstring
        logger.warning("geolocation lookup for %s failed: %s", ip, exc)
        return None
    code = (result.country_code or "").upper()
    return code if len(code) == 2 else None
