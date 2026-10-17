"""LLM configuration, multi-turn message building and the DeepSeek tool-call protocol.

The model path is optional, but these things must not regress:
  * `.env.example`'s placeholder must not look like a configured key (it used to make
    every chat perform a doomed API call before falling back),
  * recent turns sent by the browser must be normalised into the alternating,
    text-only shape both APIs expect, with hard caps,
  * the DeepSeek request must use the documented OpenAI-compatible wire format, echo
    tool calls / `reasoning_content` back, and never write a transaction by itself.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402

import check_llm  # noqa: E402
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


class DotenvEncodingTest(unittest.TestCase):
    """Windows editors save `.env` as UTF-8+BOM or ANSI/GBK; both must still load."""

    def load(self, raw):
        handle, path = tempfile.mkstemp(suffix=".env")
        os.close(handle)
        self.addCleanup(os.remove, path)
        with open(path, "wb") as f:
            f.write(raw)
        config._load_dotenv(path)

    def test_utf8_with_bom_does_not_pollute_the_key_name(self):
        name = "DSH_TEST_BOM_KEY"
        self.addCleanup(os.environ.pop, name, None)
        self.load(("\ufeff%s=sk-bom\n" % name).encode("utf-8"))
        self.assertEqual(os.environ.get(name), "sk-bom")

    def test_gbk_file_with_chinese_comments_is_read(self):
        name = "DSH_TEST_GBK_KEY"
        self.addCleanup(os.environ.pop, name, None)
        self.load(("# 中文注释\n%s=sk-gbk\n" % name).encode("gbk"))
        self.assertEqual(os.environ.get(name), "sk-gbk")

    def test_quoted_values_are_unquoted(self):
        name = "DSH_TEST_QUOTED_KEY"
        self.addCleanup(os.environ.pop, name, None)
        self.load(('%s = "sk-quoted"\n' % name).encode("utf-8"))
        self.assertEqual(os.environ.get(name), "sk-quoted")


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
        self.assertEqual(resp.get_json(), {"text": "ok", "source": "llm"})
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


class ProviderResolutionTest(unittest.TestCase):
    """`AI_PROVIDER` and the key names must resolve the way the README documents."""

    def test_deepseek_is_the_default_backend(self):
        self.assertEqual(config.resolve_provider({}), "deepseek")
        self.assertEqual(config.DEFAULT_MODELS["deepseek"], "deepseek-flash")
        self.assertEqual(config.DEFAULT_BASE_URLS["deepseek"], "https://api.deepseek.com")

    def test_provider_is_inferred_from_the_key_that_is_present(self):
        self.assertEqual(config.resolve_provider({"DEEPSEEK_API_KEY": "sk-ds"}), "deepseek")
        self.assertEqual(config.resolve_provider({"ANTHROPIC_API_KEY": "sk-ant"}), "anthropic")
        self.assertEqual(
            config.resolve_provider(
                {"DEEPSEEK_API_KEY": "sk-ds", "ANTHROPIC_API_KEY": "sk-ant"}
            ),
            "deepseek",
        )

    def test_a_lone_generic_key_still_means_claude(self):
        # Legacy deployments only ever set AI_API_KEY, so it must not switch to DeepSeek.
        self.assertEqual(config.resolve_provider({"AI_API_KEY": "sk-legacy"}), "anthropic")
        self.assertEqual(
            config.resolve_api_key("anthropic", {"AI_API_KEY": "sk-legacy"}), "sk-legacy"
        )

    def test_placeholders_never_select_a_provider(self):
        # A copied .env.example must not override a real Claude key with its placeholder.
        self.assertEqual(
            config.resolve_provider(
                {
                    "DEEPSEEK_API_KEY": "Enter DeepSeek API Key",
                    "AI_API_KEY": "sk-ant-real",
                }
            ),
            "anthropic",
        )
        self.assertEqual(
            config.resolve_api_key(
                "deepseek",
                {
                    "DEEPSEEK_API_KEY": "Enter DeepSeek API Key",
                    "AI_API_KEY": "sk-generic",
                },
            ),
            "sk-generic",
        )
        self.assertEqual(
            config.resolve_provider({"ANTHROPIC_API_KEY": "Enter Anthropic API Key"}),
            "deepseek",
        )

    def test_explicit_provider_wins_and_openai_is_a_deepseek_alias(self):
        self.assertEqual(
            config.resolve_provider({"AI_PROVIDER": "Anthropic", "DEEPSEEK_API_KEY": "sk"}),
            "anthropic",
        )
        self.assertEqual(config.resolve_provider({"AI_PROVIDER": "openai"}), "deepseek")
        # An unknown spelling falls back to inference instead of crashing the app.
        self.assertEqual(config.resolve_provider({"AI_PROVIDER": "nope"}), "deepseek")

    def test_provider_specific_key_wins_over_the_generic_one(self):
        keys = {"DEEPSEEK_API_KEY": "sk-ds", "AI_API_KEY": "sk-generic"}
        self.assertEqual(config.resolve_api_key("deepseek", keys), "sk-ds")
        # Claude has no key of its own in this environment, so the generic one applies.
        self.assertEqual(config.resolve_api_key("anthropic", keys), "sk-generic")

    def test_claude_keeps_its_historical_key_precedence(self):
        # The pre-DeepSeek code read AI_API_KEY before ANTHROPIC_API_KEY; keep that order.
        keys = {"AI_API_KEY": "sk-generic", "ANTHROPIC_API_KEY": "sk-ant-specific"}
        self.assertEqual(config.resolve_api_key("anthropic", keys), "sk-generic")

    def test_obvious_dummy_keys_are_not_configured(self):
        for key in (
            "",
            "   ",
            "Enter DeepSeek API Key",
            "your-deepseek-api-key",
            "<your key here>",
            "xxxxx",
        ):
            with self.subTest(key=key):
                with mock.patch.object(config, "AI_API_KEY", key):
                    self.assertFalse(llm_agent.is_configured())


class ModelResolutionTest(unittest.TestCase):
    """Model ids are provider-scoped: DeepSeek's host gets canonical ids, gateways don't."""

    def test_well_known_flash_spellings_map_to_the_stable_id(self):
        for name in (
            "deepseek-flash",
            "deepseek-v4.1-flash",
            "deepseek-v41-flash",
            "DeepSeek-V4.1-Flash",
            "deepseek-v4-flash",
            "deepseek-v4.1-flash-1017",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    config.resolve_model(name, "https://api.deepseek.com"),
                    "deepseek-flash",
                )

    def test_paths_and_case_do_not_matter_on_the_official_host(self):
        self.assertEqual(
            config.resolve_model("deepseek-v41-flash", "https://api.deepseek.com/v1/"),
            "deepseek-flash",
        )
        self.assertTrue(config.is_official_deepseek("api.deepseek.com"))
        self.assertFalse(config.is_official_deepseek("https://api.deepseek.com.evil.test"))

    def test_pro_and_unknown_ids_are_left_alone(self):
        self.assertEqual(
            config.resolve_model("deepseek-v4-pro", "https://api.deepseek.com"),
            "deepseek-v4-pro",
        )
        self.assertEqual(
            config.resolve_model("my-finetune", "https://api.deepseek.com"), "my-finetune"
        )

    def test_third_party_gateways_keep_the_configured_id(self):
        # Alibaba Cloud's catalog calls the same model `deepseek-v4.1-flash`.
        self.assertEqual(
            config.resolve_model(
                "deepseek-v4.1-flash",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            "deepseek-v4.1-flash",
        )

    def test_empty_model_falls_back_to_the_default(self):
        self.assertEqual(config.resolve_model("", "https://api.deepseek.com"), "deepseek-flash")

    def test_auto_thinking_is_local_to_the_official_host(self):
        self.assertEqual(
            config.resolve_thinking("auto", "https://api.deepseek.com"), "disabled"
        )
        self.assertEqual(
            config.resolve_thinking("auto", "https://example.test/v1"), "omitted"
        )
        self.assertEqual(config.resolve_thinking("enabled", "https://example.test"), "enabled")
        self.assertEqual(
            config.resolve_thinking("disabled", "https://api.deepseek.com"), "disabled"
        )


class ProviderStatusApiTest(support.ApiTestCase):
    def test_status_reports_deepseek_without_leaking_the_key(self):
        with mock.patch.multiple(
            config,
            AI_API_KEY="sk-deepseek-real",
            AI_PROVIDER="deepseek",
        ):
            body = self.client.get("/api/chat/status").get_json()

        self.assertTrue(body["configured"])
        self.assertEqual(body["provider"], "deepseek")
        self.assertEqual(body["label"], "DeepSeek")
        self.assertTrue(body["model"])
        self.assertIsNone(body["fallback"])
        self.assertNotIn("sk-deepseek-real", json.dumps(body))
        self.assertNotIn("api_key", body)

    def test_status_reports_the_rule_engine_without_a_key(self):
        with mock.patch.object(config, "AI_API_KEY", ""):
            body = self.client.get("/api/chat/status").get_json()

        self.assertFalse(body["configured"])
        self.assertEqual(body["fallback"], "rules")

    def test_status_reports_the_wire_model_for_an_alias(self):
        with mock.patch.multiple(
            config,
            AI_API_KEY="sk-deepseek-real",
            AI_PROVIDER="deepseek",
            AI_MODEL="deepseek-v4.1-flash",
            AI_BASE_URL="https://api.deepseek.com",
            AI_THINKING="auto",
        ):
            body = self.client.get("/api/chat/status").get_json()

        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["model_requested"], "deepseek-v4.1-flash")
        self.assertEqual(body["thinking"], "disabled")
        self.assertEqual(body["thinking_setting"], "auto")

    def test_an_unsupported_provider_is_reported_and_never_used(self):
        with mock.patch.multiple(
            config, AI_API_KEY="sk-real", AI_PROVIDER="mystery"
        ):
            body = self.client.get("/api/chat/status").get_json()
            self.assertFalse(body["configured"])
            self.assertEqual(body["label"], "Unsupported")
            with self.assertRaisesRegex(llm_agent.LLMError, "unsupported AI_PROVIDER"):
                llm_agent.build_provider()


def tool_call_reply(name, arguments, call_id="call_1"):
    """A `chat.completion` shaped reply containing exactly one tool call."""
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def text_reply(text):
    return {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ]
    }


class DeepSeekWireFormatTest(unittest.TestCase):
    """The payload/response shape must follow the documented DeepSeek API."""

    def provider(self, **overrides):
        options = dict(
            api_key="sk-test",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            timeout=5,
            max_tokens=300,
            temperature=0.7,
            thinking="disabled",
            reasoning_effort="high",
            max_retries=0,
        )
        options.update(overrides)
        return llm_agent.DeepSeekChat(**options)

    def test_tools_use_the_openai_function_format(self):
        body = self.provider().payload("sys", [{"role": "user", "content": "hi"}])

        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})
        self.assertFalse(body["stream"])
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["temperature"], 0.7)
        self.assertNotIn("reasoning_effort", body)
        self.assertTrue(all(tool["type"] == "function" for tool in body["tools"]))
        self.assertEqual(
            [tool["function"]["name"] for tool in body["tools"]],
            ["add_transaction", "get_budget_status", "get_goal_progress"],
        )
        self.assertIn("parameters", body["tools"][0]["function"])

    def test_thinking_mode_drops_temperature_and_leaves_room_for_reasoning(self):
        body = self.provider(thinking="enabled", max_tokens=100).payload("sys", [])

        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertNotIn("temperature", body)
        self.assertEqual(body["reasoning_effort"], "high")
        # Reasoning tokens count against max_tokens, so 100 would truncate the answer.
        self.assertGreaterEqual(body["max_tokens"], 2048)

    def test_endpoint_is_joined_once(self):
        self.assertEqual(
            self.provider().endpoint, "https://api.deepseek.com/chat/completions"
        )
        self.assertEqual(
            self.provider(base_url="https://api.deepseek.com/v1/").endpoint,
            "https://api.deepseek.com/v1/chat/completions",
        )

    def test_dotted_model_spelling_is_canonicalised_for_deepseek(self):
        body = self.provider(model="deepseek-v4.1-flash").payload("sys", [])
        self.assertEqual(body["model"], "deepseek-flash")

    def test_auto_thinking_sends_the_switch_on_deepseek(self):
        body = self.provider(thinking="auto").payload("sys", [])
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["temperature"], 0.7)

    def test_third_party_gateway_keeps_its_own_model_id_and_hides_thinking(self):
        provider = self.provider(
            model="deepseek-v4.1-flash",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            thinking="auto",
        )
        body = provider.payload("sys", [])
        self.assertEqual(provider.endpoint, provider.base_url + "/chat/completions")
        self.assertEqual(body["model"], "deepseek-v4.1-flash")
        self.assertNotIn("thinking", body)
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(provider.thinking_mode, "omitted")

    def test_tool_calls_are_normalised_and_echoed_back(self):
        data = tool_call_reply(
            "add_transaction", {"amount": 25, "type": "expense", "category": "food"}
        )
        provider = self.provider()
        turn = provider.parse(data)

        self.assertEqual(turn.text, "")
        self.assertEqual(turn.finish_reason, "tool_calls")
        self.assertEqual(len(turn.tool_calls), 1)
        call = turn.tool_calls[0]
        self.assertEqual(call.id, "call_1")
        self.assertEqual(call.name, "add_transaction")
        self.assertEqual(
            call.arguments, {"amount": 25, "type": "expense", "category": "food"}
        )

        messages = [{"role": "user", "content": "午饭 25"}]
        provider.append_assistant(messages, turn)
        provider.append_tool_results(
            messages, [(call, {"proposed_transaction": {"amount": 25}}, False)]
        )

        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(
            messages[1]["tool_calls"], data["choices"][0]["message"]["tool_calls"]
        )
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool_call_id"], "call_1")
        self.assertIn("proposed_transaction", json.loads(messages[2]["content"]))

    def test_reasoning_content_is_echoed_back_in_thinking_mode(self):
        data = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        "reasoning_content": "thought",
                    },
                }
            ]
        }
        messages = []
        provider = self.provider(thinking="enabled")
        provider.append_assistant(messages, provider.parse(data))
        self.assertEqual(messages[0]["reasoning_content"], "thought")

    def test_invalid_tool_arguments_become_a_tool_error(self):
        data = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "add_transaction",
                                    "arguments": "{not json",
                                },
                            }
                        ]
                    },
                }
            ]
        }
        call = self.provider().parse(data).tool_calls[0]

        self.assertIsNone(call.arguments)
        with self.assertRaisesRegex(ValueError, "tool input must be an object"):
            llm_agent._execute_tool(None, call.name, call.arguments)

    def test_unusable_responses_raise_llm_error(self):
        for payload in (
            {"error": {"message": "Authentication Fails"}},
            {"choices": []},
            "not an object",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(llm_agent.LLMError):
                    self.provider().parse(payload)


class HttpErrorMessageTest(unittest.TestCase):
    """Provider errors must be readable and must never leak the key."""

    def test_status_hint_and_provider_detail_are_included(self):
        message = llm_agent._http_error_message(
            401, '{"error": {"message": "Authentication Fails"}}'
        )
        self.assertIn("401", message)
        self.assertIn("Authentication Fails", message)
        self.assertIn("API key", message)
        self.assertNotIn("Bearer", message)

    def test_non_json_error_bodies_are_trimmed(self):
        message = llm_agent._http_error_message(503, "<html>" + "x" * 1000 + "</html>")
        self.assertIn("503", message)
        self.assertLessEqual(len(message), 400)


class DeepSeekChatLoopTest(support.ApiTestCase):
    """The shared tool loop over a faked DeepSeek transport."""

    def settings(self, context=False):
        return mock.patch.multiple(
            config,
            AI_API_KEY="sk-deepseek-real",
            AI_PROVIDER="deepseek",
            AI_CONTEXT=context,
        )

    def test_tool_call_then_reply_returns_text_and_proposal(self):
        posted = []
        replies = [
            tool_call_reply(
                "add_transaction",
                {"amount": 25, "type": "expense", "category": "food"},
            ),
            text_reply("已备好一笔 25 元的餐饮支出，待你确认。"),
        ]

        def fake_post(url, payload, api_key, timeout, max_retries=0):
            posted.append((url, payload, api_key))
            return replies.pop(0)

        with self.settings(), mock.patch.object(
            llm_agent, "_post_json", side_effect=fake_post
        ), app_module.app.test_request_context():
            text, proposal = llm_agent.chat_with_llm(app_module.get_db(), "午饭花了25")

        self.assertEqual(text, "已备好一笔 25 元的餐饮支出，待你确认。")
        self.assertEqual(
            proposal,
            {"amount": 25.0, "type": "expense", "category": "food", "note": ""},
        )
        self.assertEqual(len(posted), 2)
        self.assertEqual(posted[0][0], config.AI_BASE_URL + "/chat/completions")
        self.assertEqual(posted[0][2], "sk-deepseek-real")

        follow_up = posted[1][1]["messages"]
        self.assertEqual(follow_up[-2]["role"], "assistant")
        self.assertEqual(follow_up[-1]["role"], "tool")
        self.assertEqual(follow_up[-1]["tool_call_id"], "call_1")

    def test_invalid_tool_arguments_are_reported_back_to_the_model(self):
        posted = []
        bad_call = {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "add_transaction",
                                    "arguments": "{not json",
                                },
                            }
                        ]
                    },
                }
            ]
        }
        replies = [bad_call, text_reply("请问金额是多少？")]

        def fake_post(url, payload, api_key, timeout, max_retries=0):
            posted.append(payload)
            return replies.pop(0)

        with self.settings(), mock.patch.object(
            llm_agent, "_post_json", side_effect=fake_post
        ), app_module.app.test_request_context():
            text, proposal = llm_agent.chat_with_llm(app_module.get_db(), "午饭")

        self.assertEqual(proposal, None)
        self.assertEqual(text, "请问金额是多少？")
        tool_message = posted[1]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("error", json.loads(tool_message["content"]))

    def test_system_prompt_carries_the_budget_snapshot(self):
        posted = []

        def fake_post(url, payload, api_key, timeout, max_retries=0):
            posted.append(payload)
            return text_reply("ok")

        with self.settings(context=True), mock.patch.object(
            llm_agent, "_post_json", side_effect=fake_post
        ), app_module.app.test_request_context():
            llm_agent.chat_with_llm(app_module.get_db(), "我的预算怎么样")

        system = posted[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("Current date:", system["content"])
        self.assertIn("Weekly budget", system["content"])
        self.assertIn("Savings goals:", system["content"])
        self.assertIn("safety threshold", system["content"])

    def test_empty_reply_raises_so_the_route_can_fall_back(self):
        with self.settings(), mock.patch.object(
            llm_agent, "_post_json", return_value=text_reply("   ")
        ), app_module.app.test_request_context():
            with self.assertRaisesRegex(llm_agent.LLMError, "empty reply"):
                llm_agent.chat_with_llm(app_module.get_db(), "hi")


class ChatApiDeepSeekTest(support.ApiTestCase):
    """End-to-end: a DeepSeek tool call becomes a confirmation card, not a ledger write."""

    def settings(self):
        return mock.patch.multiple(
            config,
            AI_API_KEY="sk-deepseek-real",
            AI_PROVIDER="deepseek",
            AI_CONTEXT=False,
        )

    def test_tool_call_becomes_a_confirmation_card(self):
        replies = [
            tool_call_reply(
                "add_transaction",
                {"amount": 25, "type": "expense", "category": "food"},
            ),
            text_reply("已记下这笔 25 元的餐饮支出，请确认。"),
        ]

        with self.settings(), mock.patch.object(
            llm_agent, "_post_json", side_effect=lambda *args, **kwargs: replies.pop(0)
        ):
            resp = self.client.post("/api/chat", json={"message": "午饭花了25"})

        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["source"], "llm")
        self.assertEqual(body["text"], "已记下这笔 25 元的餐饮支出，请确认。")
        self.assertEqual(body["action"]["payload"]["amount"], 25)
        self.assertEqual(body["action"]["payload"]["category"], "food")
        self.assertEqual(self.count_transactions(), 0)

        confirmed = self.client.post(
            "/api/chat/action",
            json={"id": body["action"]["id"], "decision": "confirm"},
        )
        self.assertEqual(confirmed.status_code, 201)
        self.assertEqual(self.count_transactions(), 1)

    def test_a_provider_error_falls_back_to_the_rules_engine(self):
        with self.settings(), mock.patch.object(
            llm_agent,
            "_post_json",
            side_effect=llm_agent.LLMError(
                "AI provider error 401 (authentication failed)"
            ),
        ):
            with self.assertLogs("app", level="WARNING"):
                resp = self.client.post(
                    "/api/chat", json={"message": "I spent 30 on food"}
                )

        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body["source"], "rules")
        self.assertEqual(body["key"], "chat_action_preview")


class AnthropicCompatibilityTest(unittest.TestCase):
    """The pre-DeepSeek Claude integration must keep its defaults."""

    def test_claude_keeps_its_model_cap_and_timeout(self):
        with mock.patch.multiple(
            config,
            AI_PROVIDER="anthropic",
            AI_API_KEY="sk-ant-real",
            AI_MODEL="claude-sonnet-5",
            AI_MAX_TOKENS=config.DEFAULT_MAX_TOKENS["anthropic"],
            AI_TIMEOUT=config.DEFAULT_TIMEOUTS["anthropic"],
            AI_BASE_URL="https://api.anthropic.com",
        ):
            provider = llm_agent.AnthropicChat()
            built = llm_agent.build_provider()

        self.assertEqual(config.DEFAULT_MAX_TOKENS["anthropic"], 400)
        self.assertIsNone(config.DEFAULT_TIMEOUTS["anthropic"])
        self.assertEqual(provider.model, "claude-sonnet-5")
        self.assertEqual(provider.max_tokens, 400)
        self.assertIsNone(provider.timeout)
        self.assertEqual(built.name, "anthropic")

    def test_claude_model_names_are_never_rewritten(self):
        for name in ("claude-sonnet-5", "claude-opus-4-1", "claude-3-7-sonnet"):
            with self.subTest(name=name):
                self.assertEqual(
                    config.resolve_model(name, "https://api.anthropic.com"), name
                )

    def test_claude_sdk_call_keeps_its_original_options(self):
        fake_client = mock.Mock()
        fake_client.messages.create.return_value = mock.Mock(
            content=[], stop_reason="stop"
        )

        def build(**overrides):
            settings = dict(
                AI_PROVIDER="anthropic",
                AI_API_KEY="sk-ant-real",
                AI_MODEL="claude-sonnet-5",
                AI_MAX_TOKENS=400,
                AI_TIMEOUT=None,
                AI_BASE_URL="https://api.anthropic.com",
            )
            settings.update(overrides)
            fake_client.messages.create.reset_mock()
            with mock.patch.multiple(config, **settings), mock.patch(
                "anthropic.Anthropic", return_value=fake_client
            ) as factory:
                llm_agent.AnthropicChat().complete(
                    "sys", [{"role": "user", "content": "hi"}]
                )
            return factory.call_args.kwargs, fake_client.messages.create.call_args.kwargs

        # Defaults: exactly the pre-DeepSeek call — key only, SDK timeout, SDK base URL.
        sdk_options, request = build()
        self.assertEqual(sdk_options, {"api_key": "sk-ant-real"})
        self.assertEqual(request["model"], "claude-sonnet-5")
        self.assertEqual(request["max_tokens"], 400)
        self.assertEqual(request["system"], "sys")
        # The base system prompt is the original one, verbatim (the budget snapshot is
        # appended separately and can be switched off with AI_CONTEXT=false).
        self.assertTrue(
            llm_agent.SYSTEM_PROMPT.startswith(
                "You are CampusWallet's budgeting assistant"
            )
        )
        self.assertNotIn("snapshot", llm_agent.SYSTEM_PROMPT)
        # The Anthropic tool wire format is unchanged: input_schema, not parameters.
        self.assertEqual(request["tools"][0]["name"], "add_transaction")
        self.assertIn("input_schema", request["tools"][0])
        self.assertNotIn("parameters", request["tools"][0])

        # Explicit overrides are forwarded.
        sdk_options, _ = build(
            AI_BASE_URL="https://gateway.example/anthropic", AI_TIMEOUT=20
        )
        self.assertEqual(
            sdk_options,
            {
                "api_key": "sk-ant-real",
                "base_url": "https://gateway.example/anthropic",
                "timeout": 20,
            },
        )


class CheckScriptTest(unittest.TestCase):
    """`check_llm.py` is the documented way to verify a key, so its contract is pinned."""

    def status(self, configured):
        return {
            "configured": configured,
            "provider": "deepseek",
            "label": "DeepSeek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "thinking": "disabled",
            "thinking_setting": "auto",
        }

    def test_unconfigured_run_explains_what_to_do(self):
        buffer = io.StringIO()
        with mock.patch.object(
            llm_agent, "provider_status", return_value=self.status(False)
        ), contextlib.redirect_stdout(buffer):
            code = check_llm.main()

        self.assertEqual(code, 1)
        self.assertIn("DEEPSEEK_API_KEY", buffer.getvalue())

    def test_successful_call_returns_zero(self):
        provider = mock.Mock()
        provider.complete.return_value = llm_agent.Turn(text="OK")
        buffer = io.StringIO()
        with mock.patch.object(
            llm_agent, "provider_status", return_value=self.status(True)
        ), mock.patch.object(
            llm_agent, "build_provider", return_value=provider
        ), contextlib.redirect_stdout(buffer):
            code = check_llm.main()

        self.assertEqual(code, 0)
        self.assertIn("OK", buffer.getvalue())

    def test_provider_error_returns_nonzero(self):
        provider = mock.Mock()
        provider.complete.side_effect = llm_agent.LLMError("401 authentication failed")
        buffer = io.StringIO()
        with mock.patch.object(
            llm_agent, "provider_status", return_value=self.status(True)
        ), mock.patch.object(
            llm_agent, "build_provider", return_value=provider
        ), contextlib.redirect_stdout(buffer):
            code = check_llm.main()

        self.assertEqual(code, 1)
        self.assertIn("401", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
