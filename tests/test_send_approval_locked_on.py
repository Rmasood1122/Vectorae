"""Daily send approval is LOCKED ON outside the test environment.

Terms section 3 and Privacy Policy section 3 say Vectorae sends "only after
you approve each day's sending". Before this guard an admin could switch the
gate off (system setting daily_send_approval_enabled) or leave email out of
daily_send_approval_channels, and automatic email would go out unapproved.
Now neither setting has any effect in a deployed environment.
"""

import pytest
from sqlalchemy import select

from app.db import models as m
from app.services import daily_send_approval as dsa
from app.services import system_settings
from tests.conftest import NOW

DEPLOYED = ("production", "staging", "development", "", "Prod")

# conftest's autouse daily_send_approval_off replaces dsa.enabled for every
# suite; this module tests the real function, captured at import.
_REAL_ENABLED = dsa.enabled


@pytest.fixture(autouse=True)
def real_gate(monkeypatch):
    monkeypatch.setattr(dsa, "enabled", _REAL_ENABLED)


def _deployed(monkeypatch, env):
    from app.config import settings
    monkeypatch.setattr(settings, "app_env", env)


def _switched_off(db):
    system_settings.set(db, dsa.FLAG, False)
    system_settings.set(db, dsa.CHANNELS_SETTING, "linkedin")
    db.commit()


@pytest.mark.parametrize("env", DEPLOYED)
def test_no_setting_turns_the_gate_off_or_ungates_email(db_session, monkeypatch, env):
    _switched_off(db_session)
    _deployed(monkeypatch, env)
    assert dsa.locked_on()
    assert dsa.enabled(db_session) is True
    assert dsa.gated_channels(db_session) == dsa.ALL_CHANNELS


def test_an_automatic_email_is_held_in_production_with_the_switch_off(
        db_session, enrolled, verified_strategy, monkeypatch):
    _switched_off(db_session)
    _deployed(monkeypatch, "production")
    message = db_session.execute(select(m.Message)).scalars().first()
    assert message.channel is m.ChannelType.EMAIL
    assert dsa.send_hold(db_session, message, verified_strategy, NOW) is not None


def test_the_switch_still_works_under_test_only(db_session):
    """Control: the gate's own on/off behaviour stays testable."""
    _switched_off(db_session)
    assert not dsa.locked_on()
    assert dsa.enabled(db_session) is False
    assert dsa.gated_channels(db_session) == ("linkedin",)
