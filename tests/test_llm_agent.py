"""LLM configuration and multi-turn message building.

The model path is optional, but two things must not regress:
  * `.env.example`'s placeholder must not look like a configured key (it used to make
    every chat perform a doomed API call before falling back), and
  * recent turns sent by the browser must be normalised into the alternating,
    text-only shape the Messages API expects, with hard caps.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402

import config  # noqa: E402
import limits  # noqa: E402
import llm_agent  # noqa: E402

app_module = support.app_module


class ApiKeyConfigTest(unittest.TestCase):
    def test_placeholder_keys_are_not_configured(self):
        for key in ("", "   ", "Enter AI API", "your-api-key", "changeme", "sk-..."):
            with self.subTest(key=key):
                with mock.patch.object(config, "AI_API_KEY", key):
                    self.assertFalse(llm_agent.is_configured())

    def test_real_looking_key_is_configured(self):
        with mock.patch.object(config, "AI_API_KEY", "sk-ant-real-key"):
            self.assertTrue(llm_agent.is_configured())


class BuildMessagesTest(unittest.TestCase):
    def test_no_history_is_one_user_turn(self):
        self.assertEqual(
            llm_agent.build_messages("hi"),
            [{"role": "user", "content": "hi"}],
        )

    def test_leading_assistant_is_dropped(self):
        history = [
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "hi"},
        ]
        self.assertEqual(
            llm_agent.build_messages("there", history),
            [{"role": "user", "content": "hi\nthere"}],
        )

    def test_consecutive_roles_are_merged(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ]
        self.assertEqual(
            llm_agent.build_messages("d", history),
            [
                {"role": "user", "content": "a\nb"},
                {"role": "assistant", "content": "c"},
                {"role": "user", "content": "d"},
            ],
        )

    def test_invalid_history_entries_are_ignored(self):
        history = [
            {"role": "system", "content": "nope"},
            {"role": "user", "content": 123},
            "not a dict",
            {"role": "assistant", "content": "  ok  "},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": ""},
        ]
        self.assertEqual(
            llm_agent.build_messages("there", history),
            [{"role": "user", "content": "hi\nthere"}],
        )

    def test_history_is_capped_and_starts_with_user(self):
        history = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": str(index),
            }
            for index in range(40)
        ]
        messages = llm_agent.build_messages("now", history)
        self.assertLessEqual(len(messages), llm_agent.MAX_HISTORY_MESSAGES)
        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("now", messages[-1]["content"])

    def test_message_length_is_capped(self):
        messages = llm_agent.build_messages("x" * 5000)
        self.assertLessEqual(len(messages[-1]["content"]), llm_agent.MAX_HISTORY_CHARS)


class ToolInputValidationTest(unittest.TestCase):
    """Malformed model tool input must become ValueError, not AttributeError/IntegrityError."""

    def test_tool_input_must_be_an_object(self):
        for value in (None, [], "x"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "tool input must be an object"):
                    llm_agent._execute_tool(None, "add_transaction", value)

    def test_category_must_be_a_string(self):
        with self.assertRaisesRegex(ValueError, "category must be a string"):
            llm_agent._execute_tool(
                None,
                "add_transaction",
                {"amount": 10, "type": "expense", "category": 123},
            )

    def test_category_and_note_are_length_checked(self):
        with self.assertRaisesRegex(ValueError, "category is too long"):
            llm_agent._execute_tool(
                None,
                "add_transaction",
                {
                    "amount": 10,
                    "type": "expense",
                    "category": "c" * (limits.MAX_CATEGORY_LENGTH + 1),
                },
            )
        with self.assertRaisesRegex(ValueError, "note is too long"):
            llm_agent._execute_tool(
                None,
                "add_transaction",
                {
                    "amount": 10,
                    "type": "expense",
                    "category": "food",
                    "note": "n" * (limits.MAX_NOTE_LENGTH + 1),
                },
            )

    def test_note_must_be_a_string(self):
        with self.assertRaisesRegex(ValueError, "note must be a string"):
            llm_agent._execute_tool(
                None,
                "add_transaction",
                {
                    "amount": 10,
                    "type": "expense",
                    "category": "food",
                    "note": {"text": "x"},
                },
            )

    def test_missing_note_defaults_to_empty(self):
        result = llm_agent._execute_tool(
            None,
            "add_transaction",
            {"amount": 10, "type": "expense", "category": "food", "note": None},
        )
        self.assertEqual(result["proposed_transaction"]["note"], "")

    def test_budget_period_must_be_known(self):
        with self.assertRaisesRegex(ValueError, "period must be weekly or monthly"):
            llm_agent._execute_tool(None, "get_budget_status", {"period": "yearly"})

    def test_goal_name_filter_must_be_a_string(self):
        with self.assertRaisesRegex(ValueError, "name must be a string"):
            llm_agent._execute_tool(None, "get_goal_progress", {"name": 123})


class ChatHistoryApiTest(support.ApiTestCase):
    def test_history_must_be_an_array(self):
        resp = self.client.post("/api/chat", json={"message": "hi", "history": {}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "history must be an array")

    def test_history_is_forwarded_to_the_model(self):
        history = [{"role": "user", "content": "earlier"}]
        with mock.patch.object(
            app_module.llm_agent, "is_configured", return_value=True
        ), mock.patch.object(
            app_module.llm_agent, "chat_with_llm", return_value=("ok", None)
        ) as chat:
            resp = self.client.post(
                "/api/chat", json={"message": "hello", "history": history}
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"text": "ok"})
        args = chat.call_args[0]
        self.assertEqual(args[1], "hello")
        self.assertEqual(args[2], history)

    def test_history_is_optional(self):
        with mock.patch.object(
            app_module.llm_agent, "is_configured", return_value=True
        ), mock.patch.object(
            app_module.llm_agent, "chat_with_llm", return_value=("ok", None)
        ) as chat:
            resp = self.client.post("/api/chat", json={"message": "hello"})

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(chat.call_args[0][2])

    def test_model_failure_logs_and_falls_back_to_the_rules_engine(self):
        with mock.patch.object(
            app_module.llm_agent, "is_configured", return_value=True
        ), mock.patch.object(
            app_module.llm_agent, "chat_with_llm", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs("app", level="WARNING"):
                resp = self.client.post(
                    "/api/chat", json={"message": "I spent 30 on food"}
                )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["key"], "chat_action_preview")


if __name__ == "__main__":
    unittest.main()
