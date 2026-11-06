"""The image must install what the tests validate.

WHAT WENT WRONG. The Dockerfile installs with `pip install .`, which reads
pyproject.toml. CI and local runs install requirements.txt. The two files had
drifted: requirements.txt pinned `sqlalchemy>=2.0.30,<2.1`, pyproject.toml said
`sqlalchemy>=2.0`. The image therefore resolved SQLAlchemy 2.1, where a plain
`postgresql://` URL selects the psycopg (v3) driver -- which the image does not
install (it has psycopg2-binary). app/db/base.py builds its engine at import
time, so Celery beat died with `ModuleNotFoundError: No module named 'psycopg'`
on every deploy, while every test passed.

WHAT THIS PINS.
  * building an engine for a `postgresql://` URL must find an installed DBAPI.
    No connection is made -- construction alone is where production died;
  * pyproject.toml and requirements.txt must not disagree on any package they
    both declare, so the image cannot again resolve a version the tests never
    ran against.

Self-contained on purpose (no fixtures), so it can also be run with
`--noconftest` inside an environment built from pyproject.toml alone.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parent.parent

#: The URL shape production uses: no explicit driver.
POSTGRES_URL = "postgresql://user:password@db.internal:5432/leadpilot"


# --------------------------------------------------------------------------
# The engine can be built
# --------------------------------------------------------------------------


def test_a_postgresql_url_resolves_to_an_installed_driver():
    """Exactly what app/db/base.py does at import. Raises nothing, connects
    to nothing."""
    from sqlalchemy import create_engine

    try:
        engine = create_engine(POSTGRES_URL, pool_pre_ping=True, future=True)
    except ModuleNotFoundError as exc:  # pragma: no cover - the regression
        pytest.fail(
            f"create_engine({POSTGRES_URL.split('://')[0]}://...) needs a DBAPI "
            f"that is not installed: {exc}. SQLAlchemy and the PostgreSQL "
            f"driver in this environment do not match."
        )
    assert engine.dialect.name == "postgresql"
    engine.dispose()


def test_the_driver_it_resolves_to_is_the_one_we_ship():
    """psycopg2-binary is what both dependency files declare, so that is the
    driver a bare postgresql:// URL has to select."""
    from sqlalchemy import create_engine

    engine = create_engine(POSTGRES_URL)
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.dialect.dbapi.__name__ == "psycopg2"
    finally:
        engine.dispose()


# --------------------------------------------------------------------------
# The two dependency files agree
# --------------------------------------------------------------------------


def _parse(spec: str) -> Requirement | None:
    spec = spec.split("#", 1)[0].strip()
    if not spec or spec.startswith("-"):
        return None
    return Requirement(spec)


def _requirements_txt() -> dict:
    """{canonical name: [Requirement, ...]} -- a package may be listed twice."""
    found: dict = {}
    for line in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines():
        requirement = _parse(line)
        if requirement is not None:
            found.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    return found


def _pyproject() -> dict:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    specs = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    found: dict = {}
    for spec in specs:
        requirement = _parse(spec)
        if requirement is not None:
            found.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    return found


def _bounds(requirements: list) -> frozenset:
    """Every version constraint a file puts on one package, order-free.
    `>=2.7` and `>=2.7.0` are the same constraint."""
    out = set()
    for requirement in requirements:
        for specifier in requirement.specifier:
            version = re.sub(r"(\.0)+$", "", specifier.version)
            out.add(f"{specifier.operator}{version}")
    return frozenset(out)


def test_both_files_exist_and_parse():
    assert _requirements_txt(), "requirements.txt has no requirements"
    assert _pyproject(), "pyproject.toml has no dependencies"


def test_the_two_files_agree_on_every_package_they_share():
    requirements, pyproject = _requirements_txt(), _pyproject()
    shared = sorted(set(requirements) & set(pyproject))
    assert shared, "the two files share no package -- the parser is broken"

    disagreements = [
        f"{name}: requirements.txt {sorted(_bounds(requirements[name])) or ['(any)']} "
        f"vs pyproject.toml {sorted(_bounds(pyproject[name])) or ['(any)']}"
        for name in shared
        if _bounds(requirements[name]) != _bounds(pyproject[name])
    ]
    assert not disagreements, (
        "pyproject.toml (what the image installs) and requirements.txt (what "
        "the tests run on) disagree:\n  " + "\n  ".join(disagreements)
    )


@pytest.mark.parametrize("package", ["sqlalchemy", "fastapi", "celery", "redis",
                                     "alembic", "uvicorn", "pydantic"])
def test_core_runtime_packages_are_capped_in_both_files(package):
    """An open-ended range is how the image drifted to a new major/minor
    line the tests had never seen."""
    for label, declared in (("requirements.txt", _requirements_txt()),
                            ("pyproject.toml", _pyproject())):
        operators = {spec.operator for req in declared[package] for spec in req.specifier}
        assert operators & {"<", "<=", "==", "~="}, (
            f"{package} has no upper bound in {label}: "
            f"{[str(r) for r in declared[package]]}")


# --------------------------------------------------------------------------
# The Celery broker transport can talk to the redis-py we allow
# --------------------------------------------------------------------------

#: kombu release -> the oldest redis-py its Redis transport accepts. A kombu
#: newer than this table must be added here before it may be allowed.
_KOMBU_MIN_REDIS = (("5.7.0", "6.1.0"),)


def test_every_allowed_kombu_works_with_every_allowed_redis():
    """2026-11-06 outage. kombu was not declared, so `pip install .` took the
    newest release Celery allows: kombu 5.7.0, whose Redis transport refuses
    redis-py < 6.1. redis is capped below 6, so every broker connection
    raised VersionMismatch: the worker and beat crash-looped and every
    strategy launch returned 500, while every test passed (tests run on the
    pinned lockfile versions)."""
    for label, declared in (("requirements.txt", _requirements_txt()),
                            ("pyproject.toml", _pyproject())):
        assert "kombu" in declared, f"kombu is not declared in {label}"
        kombu = declared["kombu"][0].specifier
        redis = declared["redis"][0].specifier
        for kombu_version, min_redis in _KOMBU_MIN_REDIS:
            if kombu.contains(kombu_version):
                too_old = [v for v in ("5.3.1", "6.0.0") if redis.contains(v)
                           and Version(v) < Version(min_redis)]
                assert not too_old, (
                    f"{label} allows kombu {kombu_version} (needs redis-py >= "
                    f"{min_redis}) and redis {too_old}: {kombu} / {redis}")


def test_the_installed_kombu_accepts_the_installed_redis():
    """The same check against what this environment actually installed."""
    import kombu
    import redis

    for kombu_version, min_redis in _KOMBU_MIN_REDIS:
        if Version(kombu.__version__) >= Version(kombu_version):
            assert Version(redis.__version__) >= Version(min_redis), (
                f"kombu {kombu.__version__} needs redis-py >= {min_redis}, "
                f"installed {redis.__version__}")
