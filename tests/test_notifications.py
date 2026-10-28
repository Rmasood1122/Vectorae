"""
test_notifications.py — Vectorae (M7 Chunk 4)

Tests for app/services/notifications.py.
firebase_admin is mocked throughout — no real Firebase connection needed.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ── Mock firebase_admin before the module under test imports it ───────────────
# This prevents ImportError if firebase-admin isn't installed in the test env.
_mock_messaging = MagicMock()
_mock_firebase_app = MagicMock()

sys.modules.setdefault('firebase_admin', MagicMock(
    initialize_app=MagicMock(return_value=_mock_firebase_app),
    messaging=_mock_messaging,
    credentials=MagicMock(Certificate=MagicMock()),
    exceptions=MagicMock(InvalidArgumentError=Exception),
))
sys.modules.setdefault('firebase_admin.messaging', _mock_messaging)
sys.modules.setdefault('firebase_admin.credentials', sys.modules['firebase_admin'].credentials)

from app.services import notifications  # noqa: E402 (import after mock)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_firebase_app():
    """Reset the lazy Firebase singleton and the module-global messaging mock.

    `_mock_messaging` is installed in sys.modules once, so its call counts and
    side effects live for the whole session. `reset_mock()` on its own does NOT
    clear `side_effect` or `return_value` -- a test that set
    `side_effect = ConnectionError(...)` leaked it into every later test, and a
    test that never requests `mock_send` inherited the previous test's call
    count. Both of those were live failures here.
    """
    notifications._firebase_app = None
    _mock_messaging.reset_mock(side_effect=True, return_value=True)
    yield
    notifications._firebase_app = None
    _mock_messaging.reset_mock(side_effect=True, return_value=True)


@pytest.fixture
def mock_send():
    """Patch firebase_admin.messaging.send for all tests that need it."""
    _mock_messaging.send.reset_mock(side_effect=True, return_value=True)
    _mock_messaging.send.return_value = 'projects/test/messages/abc123'
    _mock_messaging.Message = MagicMock()
    _mock_messaging.Notification = MagicMock()
    _mock_messaging.AndroidConfig = MagicMock()
    _mock_messaging.AndroidNotification = MagicMock()
    return _mock_messaging.send


@pytest.fixture
def mock_db():
    """Mock SQLAlchemy session.

    SYNC, because that is what the application has: `get_db` yields a
    Session and SessionLocal is sync. notifications.py used to annotate its
    db parameter as AsyncSession and `await db.execute(...)` it, which could
    never have run against a real session - the same defect that made the
    /devices router dead code. These tests encoded that unrunnable contract.
    """
    db = MagicMock()
    db.execute = MagicMock()
    db.commit = MagicMock()
    return db


def _token_rows(*tokens):
    """Shape returned by session.execute(select(...)).all()."""
    result = MagicMock()
    result.all.return_value = [(t,) for t in tokens]
    return result


# ── send_to_device ────────────────────────────────────────────────────────────

class TestSendToDevice:
    @pytest.mark.asyncio
    async def test_success_returns_true(self, mock_send):
        with patch.dict('os.environ', {'FIREBASE_CREDENTIALS_PATH': '/fake/creds.json'}):
            result = await notifications.send_to_device(
                token='valid_token_abc',
                title='Test title',
                body='Test body',
                data={'deepLink': 'clienthunter:///strategies/1'},
            )
        assert result is True
        mock_send.assert_called_once()
        # Verify the message was constructed with the correct token
        call_args = _mock_messaging.Message.call_args
        assert call_args.kwargs.get('token') == 'valid_token_abc'

    @pytest.mark.asyncio
    async def test_unregistered_token_marks_invalid_in_db(self, mock_send, mock_db):
        mock_send.side_effect = Exception('unregistered token error from Firebase')

        with patch.dict('os.environ', {'FIREBASE_CREDENTIALS_PATH': '/fake/creds.json'}):
            result = await notifications.send_to_device(
                token='stale_token_xyz',
                title='Test',
                body='Test',
                db=mock_db,
            )

        assert result is False
        # DB should have been called to mark the token invalid
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_network_error_returns_false_no_crash(self, mock_send):
        """A transient network error must NOT crash the calling service."""
        mock_send.side_effect = ConnectionError('network timeout')

        with patch.dict('os.environ', {'FIREBASE_CREDENTIALS_PATH': '/fake/creds.json'}):
            result = await notifications.send_to_device(
                token='any_token',
                title='Test',
                body='Test',
            )

        assert result is False  # graceful failure, no exception raised

    @pytest.mark.asyncio
    async def test_missing_firebase_credentials_raises_runtime_error(self, mock_send):
        """If FIREBASE_CREDENTIALS_PATH is not set, raise a clear error at init time."""
        with patch.dict('os.environ', {}, clear=True):
            # Remove FIREBASE_CREDENTIALS_PATH from env
            import os
            os.environ.pop('FIREBASE_CREDENTIALS_PATH', None)
            with pytest.raises(RuntimeError, match='FIREBASE_CREDENTIALS_PATH'):
                await notifications.send_to_device('token', 'title', 'body')


# ── send_to_user ──────────────────────────────────────────────────────────────

class TestSendToUser:
    @pytest.mark.asyncio
    async def test_fan_out_to_multiple_tokens(self, mock_send, mock_db):
        """send_to_user fans out to all valid tokens for the user."""
        from app.db.models import DeviceToken

        # Mock DB returning 3 valid tokens
        mock_db.execute.return_value = _token_rows('token_1', 'token_2', 'token_3')

        with patch.dict('os.environ', {'FIREBASE_CREDENTIALS_PATH': '/fake/creds.json'}):
            sent = await notifications.send_to_user(
                user_id=1,
                title='Test',
                body='Test body',
                db=mock_db,
            )

        assert sent == 3
        assert mock_send.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_tokens(self, mock_db):
        """No tokens → returns 0, no FCM calls."""
        mock_db.execute.return_value = _token_rows()

        sent = await notifications.send_to_user(
            user_id=99,
            title='Test',
            body='Test',
            db=mock_db,
        )

        assert sent == 0
        _mock_messaging.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_opens_its_own_session_when_db_is_none(self, mock_db):
        """db=None must OPEN a session, not give up.

        It used to log a warning and return 0, so every caller without a
        session handy - Celery tasks, webhook handlers - silently sent
        nothing. A short-lived SessionLocal is opened and closed instead.
        """
        mock_db.execute.return_value = _token_rows()

        with patch('app.db.base.SessionLocal', return_value=mock_db) as factory:
            sent = await notifications.send_to_user(
                user_id=1,
                title='Test',
                body='Test',
                db=None,
            )

        assert sent == 0
        factory.assert_called_once()
        mock_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_partial_send_counted_correctly(self, mock_send, mock_db):
        """If 2 of 3 tokens succeed, returns 2."""
        mock_db.execute.return_value = _token_rows('t1', 't2', 't3')

        # Make the second token fail
        mock_send.side_effect = [
            'projects/x/messages/1',    # t1 success
            Exception('invalid argument'),  # t2 fail
            'projects/x/messages/3',    # t3 success
        ]

        with patch.dict('os.environ', {'FIREBASE_CREDENTIALS_PATH': '/fake/creds.json'}):
            sent = await notifications.send_to_user(
                user_id=1, title='T', body='B', db=mock_db,
            )

        assert sent == 2


# ── Notification event functions ──────────────────────────────────────────────

class TestNotificationEvents:
    """
    Each event function is tested for correct title, body, and deepLink data.
    send_to_user is patched so we inspect what it was called with.
    """

    @pytest.fixture(autouse=True)
    def patch_send_to_user(self):
        self.sent_calls: list[dict[str, Any]] = []

        async def _capture(*args, **kwargs):
            self.sent_calls.append({'args': args, 'kwargs': kwargs})
            return 1

        with patch.object(notifications, 'send_to_user', side_effect=_capture):
            yield

    @pytest.mark.asyncio
    async def test_notify_strategy_ready(self):
        await notifications.notify_strategy_ready(user_id=1, strategy_id=42)
        assert len(self.sent_calls) == 1
        kwargs = self.sent_calls[0]['kwargs']
        assert 'ready' in kwargs['title'].lower()
        assert kwargs['data']['deepLink'] == 'clienthunter:///strategies/42'

    @pytest.mark.asyncio
    async def test_notify_strategy_needs_review(self):
        await notifications.notify_strategy_needs_review(user_id=1, strategy_id=7)
        kwargs = self.sent_calls[0]['kwargs']
        assert 'review' in kwargs['title'].lower() or 'review' in kwargs['body'].lower()
        assert kwargs['data']['deepLink'] == 'clienthunter:///strategies/7'

    @pytest.mark.asyncio
    async def test_notify_new_reply_with_company(self):
        await notifications.notify_new_reply(user_id=1, lead_id=5, company='Acme Corp')
        kwargs = self.sent_calls[0]['kwargs']
        assert 'Acme Corp' in kwargs['title'] or 'Acme Corp' in kwargs['body']
        assert kwargs['data']['deepLink'] == 'clienthunter:///leads/5'

    @pytest.mark.asyncio
    async def test_notify_meeting_booked(self):
        await notifications.notify_meeting_booked(user_id=1, lead_id=3, attendee_name='Sarah Kim')
        kwargs = self.sent_calls[0]['kwargs']
        assert 'meeting' in kwargs['title'].lower() or 'booked' in kwargs['title'].lower()
        assert 'Sarah Kim' in kwargs['body']
        assert kwargs['data']['deepLink'] == 'clienthunter:///leads/3'

    @pytest.mark.asyncio
    async def test_notify_campaign_paused(self):
        await notifications.notify_campaign_paused(user_id=1, campaign_id=9)
        kwargs = self.sent_calls[0]['kwargs']
        assert 'paused' in kwargs['title'].lower()
        assert kwargs['data']['deepLink'] == 'clienthunter:///campaigns/9'

    @pytest.mark.asyncio
    async def test_notify_whatsapp_approved(self):
        await notifications.notify_whatsapp_template_status(
            user_id=1, template_name='intro_v2', approved=True
        )
        kwargs = self.sent_calls[0]['kwargs']
        assert 'approved' in kwargs['title'].lower() or 'approved' in kwargs['body'].lower()
        assert 'intro_v2' in kwargs['title'] or 'intro_v2' in kwargs['body']

    @pytest.mark.asyncio
    async def test_notify_whatsapp_rejected(self):
        await notifications.notify_whatsapp_template_status(
            user_id=1, template_name='follow_up', approved=False
        )
        kwargs = self.sent_calls[0]['kwargs']
        assert 'rejected' in kwargs['title'].lower() or 'rejected' in kwargs['body'].lower()
