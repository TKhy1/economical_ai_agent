"""Shared helpers for the unittest suite.

Each test points ``app.DB_PATH`` at a throwaway SQLite file so the real
``economical_agent.db`` is never touched. The file lives in the system temp
directory by default; set ``ECONOMICAL_AGENT_TEST_TMP`` to override it (handy in
sandboxes where only specific directories are writable).
"""
import os
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402

TEST_TMP = os.environ.get("ECONOMICAL_AGENT_TEST_TMP") or tempfile.gettempdir()


def make_db_path():
    os.makedirs(TEST_TMP, exist_ok=True)
    return os.path.join(TEST_TMP, f"economical_agent_test_{uuid.uuid4().hex}.db")


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.db_path = make_db_path()
        self._original_db_path = app_module.DB_PATH
        app_module.DB_PATH = self.db_path
        app_module.app.config.update(TESTING=True)
        app_module.init_db()
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.DB_PATH = self._original_db_path
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                os.remove(self.db_path + suffix)
            except OSError:
                pass

    def raw_connection(self):
        import sqlite3

        return sqlite3.connect(app_module.DB_PATH)

    def seed_expired_action(self):
        """Insert an already-expired ChatAction straight into the DB."""
        action_id = "expired-action"
        conn = self.raw_connection()
        conn.execute(
            "INSERT INTO ChatAction (id, user_id, action_type, payload, period_type, "
            "status, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                action_id,
                app_module.DEFAULT_USER_ID,
                "confirm_credit",
                '{"amount": 10, "type": "expense", "category": "food", "note": ""}',
                "weekly",
                "pending",
                "2000-01-01T00:00:00",
                "2000-01-01T00:00:00",
            ),
        )
        conn.commit()
        conn.close()
        return action_id

    def count_transactions(self):
        conn = self.raw_connection()
        total = conn.execute('SELECT COUNT(*) FROM "Transaction"').fetchone()[0]
        conn.close()
        return total
