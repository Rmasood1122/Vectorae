"""Daily send approval and the LinkedIn network (LEGAL/SAFETY GUARD).

Gate change 2026-11-02 (docs/program/plans/gate-linkedin-unipile-removal.md).
This file used to prove that a BULK send to the customer's own connections
(Celery jobs through Unipile) waited for today's LinkedIn approval. The bulk
and one-click sends were removed with Unipile: Vectorae never sends on
LinkedIn. What must hold now:

  * there is no route or worker task that sends a network message;
  * approving today's LinkedIn sending releases nothing on the network and
    does not approve any LinkedIn task: each task still needs its own
    approval (per-action approval, separate from the daily email approval);
  * a network message reaches LinkedIn only through a task the user opens.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import models as m
from app.services import daily_send_approval as dsa
from app.services import linkedin_tasks
from tests.conftest import NOW
from tests.test_network_messages import (  # noqa: F401
    _approved,
    claude,
    task_session,
    world,
)


def _approve(db, user, channels):
    return dsa.approve(db, user.id, actor_id=user.id, channels=channels,
                       linkedin_risk_acknowledged="linkedin" in channels,
                       linkedin_warning_version=(dsa.LINKEDIN_WARNING_VERSION
                                                 if "linkedin" in channels else None),
                       now=NOW)


@pytest.fixture(autouse=True)
def flag_on(db_session):
    from app.services import system_settings

    system_settings.set(db_session, dsa.FLAG, True)


class TestNothingIsSentByVectorae:
    def test_no_send_or_bulk_route_exists(self, client, world, claude):
        mid = _approved(client, world)
        assert client.post(f"/linkedin-network/messages/{mid}/send").status_code in (404, 405)
        assert client.post("/linkedin-network/messages/bulk-send",
                           json={"ids": [mid]}).status_code in (404, 405)

    def test_no_worker_task_sends_a_network_message(self):
        from app.workers import network_tasks

        assert not hasattr(network_tasks, "send_queued")
        assert not hasattr(network_tasks, "enqueue_send")
        assert not hasattr(dsa, "network_hold")


class TestApprovalNeverApprovesATask:
    def test_approving_linkedin_releases_nothing_on_the_network(
            self, client, world, claude, db_session):
        mid = _approved(client, world)
        out = _approve(db_session, world["user"], ["email", "linkedin"])
        assert "released_network" not in out
        msg = db_session.get(m.LinkedInNetworkMessage, uuid.UUID(str(mid)))
        assert msg.status == "approved" and msg.sent_at is None

    def test_a_task_still_needs_its_own_approval_after_daily_approval(
            self, client, world, claude, db_session):
        mid = _approved(client, world)
        task_id = client.post("/linkedin-tasks/from-network-message",
                              json={"message_id": str(mid)}).json()["id"]
        _approve(db_session, world["user"], ["linkedin"])
        with pytest.raises(linkedin_tasks.TaskError, match="Approve"):
            linkedin_tasks.open_task(db_session, world["user"], task_id, now=NOW)
        linkedin_tasks.approve(db_session, world["user"], task_id, now=NOW)
        assert linkedin_tasks.open_task(db_session, world["user"], task_id,
                                        now=NOW)["profile_url"].startswith(
            "https://www.linkedin.com/in/")
