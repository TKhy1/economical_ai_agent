"""LLM-powered chat layer (DeepSeek by default, Anthropic optional).

The model never touches financial data directly. It can only describe *what* it wants via
a tool call; every tool is executed locally against the deterministic `ledger`/`agent`
modules, which enforce the actual rules (see ledger.add_transaction). `add_transaction`
returns a *proposal* that app.py turns into a 10-minute confirmation card — the model
cannot write to the ledger.

Two backends share one tool loop:

* DeepSeek (default) — the OpenAI-compatible ``POST /chat/completions`` format with
  ``tools``. Implemented with ``urllib`` from the standard library, so no SDK is needed.
* Anthropic — the Claude Messages API through the optional ``anthropic`` SDK.

If no API key is configured, or the API call fails for any reason, `chat_with_llm` raises
and callers are expected to catch that and fall back to the rule-based parser in agent.py,
so the app keeps working without a key.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date

import agent
import config
import ledger
import limits

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are CampusWallet's budgeting assistant for a college student. Currency is CNY (¥). "
    "You must never claim to record, change, or look up financial data yourself — every such "
    "action goes through the tools provided; they are backed by a deterministic rules engine "
    "you cannot bypass. When the user describes a transaction, extract the amount, category, "
    "and type and call add_transaction to prepare a proposal. That tool never writes to the "
    "ledger; the application will apply confirmation and safety rules before any write. After "
    "a tool returns a result, write a short, warm reply in the same language as the user, "
    "citing concrete numbers without claiming a transaction was recorded unless the "
    "application confirms it."
)

# Provider-neutral tool definitions. Each backend converts `parameters` to its own wire
# format (OpenAI `function.parameters` / Anthropic `input_schema`).
TOOLS = [
    {
        "name": "add_transaction",
        "description": (
            "Prepare a transaction proposal (expense or income) for the application to "
            "validate. This tool never writes to the ledger; the application applies "
            "confirmation and safety rules before calling the canonical ledger operation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {
                    "type": "number",
                    "description": "Positive amount in CNY.",
                },
                "type": {
                    "type": "string",
                    "enum": ["income", "expense"],
                },
                "category": {
                    "type": "string",
                    "description": (
                        "One of: rent, food, groceries, utilities, transport, tuition, "
                        "healthcare, phone, entertainment, shopping, subscriptions, travel, "
                        "emergency, other, books, courses, software, career, workshop, "
                        "income, allowance, scholarship, job, parttime."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional short note.",
                },
            },
            "required": ["amount", "type", "category"],
        },
    },
    {
        "name": "get_budget_status",
        "description": (
            "Get the user's current budget status: spending vs. limit for each fund-pool "
            "tier (survival, buffer, learning), income/expense/net totals, money locked for "
            "savings goals, and the truly available (spendable) balance."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["weekly", "monthly"],
                    "description": "Budget period to inspect. Defaults to weekly.",
                }
            },
        },
    },
    {
        "name": "get_goal_progress",
        "description": (
            "Get progress toward the user's savings goals. Optionally filter by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional goal name to filter by.",
                }
            },
        },
    },
]

MAX_TOOL_ROUNDS = 5

# Chat history is client-supplied and only used as plain text context. Cap both the number of
# turns and the length of each turn so a long conversation cannot blow up the request.
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 2000

# The budget snapshot is capped too: a long goal list must not crowd out the conversation.
MAX_GOALS_IN_CONTEXT = 8

_PROVIDER_LABELS = {"deepseek": "DeepSeek", "anthropic": "Anthropic"}

_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_HTTP_HINTS = {
    400: "the request was rejected as malformed",
    401: "authentication failed - check the API key",
    402: "insufficient balance",
    403: "access denied",
    404: "endpoint not found - check AI_BASE_URL",
    413: "the request was too large",
    422: "invalid parameters",
    429: "rate limited - try again shortly",
    500: "the provider had an internal error",
    502: "bad gateway",
    503: "the provider is overloaded",
    504: "the provider timed out",
}


class LLMError(RuntimeError):
    """The chat backend could not produce a usable reply.

    Callers treat this as "fall back to the rule-based parser": it is raised for a missing
    key, an unsupported provider, a network/timeout failure, an auth/quota error, or a
    response the app cannot use (for example an empty reply).
    """


@dataclass
class ToolCall:
    """One tool call returned by the model, normalised across providers."""

    id: str
    name: str
    arguments: object = None  # dict on success, None when the model sent invalid JSON
    raw_arguments: str = ""


@dataclass
class Turn:
    """One assistant reply, normalised across providers."""

    text: str = ""
    tool_calls: list = field(default_factory=list)
    raw: object = None  # provider payload echoed back on the next request
    finish_reason: str = ""


def is_configured():
    """True when a real (non-placeholder) key for a supported provider is configured."""
    if config.normalize_provider(config.AI_PROVIDER) is None:
        return False
    return config.looks_like_key(config.AI_API_KEY)


def provider_status():
    """Secret-free description of the active backend, served by `GET /api/chat/status`.

    The key itself is never included: only whether one is configured, plus the names the
    browser needs to label the chat. `model` is the id that will actually be sent (aliases
    such as ``deepseek-v4.1-flash`` are canonicalised by `config.resolve_model`), while
    `model_requested` echoes the configured spelling when the two differ.
    """
    provider = config.normalize_provider(config.AI_PROVIDER)
    configured = is_configured()
    effective_model = config.resolve_model(config.AI_MODEL, config.AI_BASE_URL)
    status = {
        "configured": configured,
        "provider": provider or (config.AI_PROVIDER or "").strip().lower() or "unsupported",
        "label": _PROVIDER_LABELS.get(provider, "Unsupported"),
        "model": effective_model,
        "fallback": None if configured else "rules",
    }
    if effective_model != config.AI_MODEL:
        status["model_requested"] = config.AI_MODEL
    if provider == config.PROVIDER_DEEPSEEK:
        status["base_url"] = config.AI_BASE_URL
        status["thinking"] = config.resolve_thinking(
            config.AI_THINKING, config.AI_BASE_URL
        )
        status["thinking_setting"] = config.AI_THINKING
    status["context_snapshot"] = bool(config.AI_CONTEXT)
    return status


def _clean_history(history):
    """Normalise client-supplied chat history into alternating text-only turns."""
    if not isinstance(history, (list, tuple)):
        return []
    messages = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        content = content[:MAX_HISTORY_CHARS]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] = (
                messages[-1]["content"] + "\n" + content
            )[:MAX_HISTORY_CHARS]
        else:
            messages.append({"role": role, "content": content})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages[-MAX_HISTORY_MESSAGES:]


def build_messages(message, history=None):
    """Recent text turns + the current user message.

    Both supported APIs expect alternating roles, so consecutive turns are merged and a
    leading assistant turn is dropped. Tool blocks are never accepted from the client.
    """
    message = (message or "").strip()[:MAX_HISTORY_CHARS]
    messages = _clean_history(history)
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = (
            messages[-1]["content"] + "\n" + message
        )[:MAX_HISTORY_CHARS]
    else:
        messages.append({"role": "user", "content": message})
    messages = messages[-MAX_HISTORY_MESSAGES:]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


# ---------------------------------------------------------------------------
# Read-only context snapshot
# ---------------------------------------------------------------------------
def _money(value):
    amount = float(value or 0.0)
    sign = "-" if amount < 0 else ""
    return "%s¥%s" % (sign, format(abs(amount), ",.2f"))


def build_context_snapshot(db):
    """A compact, read-only summary of the student's money for the system prompt.

    It answers the common "how am I doing" questions in a single request instead of a
    tool round trip. The tools remain authoritative for fresh numbers, and this function
    never writes a transaction — `ledger.compute_budget_status` may only seed the current
    period's budget plan, exactly like the dashboard does.
    """
    today = date.today()
    profile = ledger.get_profile(db) or {}
    threshold = profile.get("security_threshold")

    lines = [
        "Read-only budget snapshot for reference only "
        "(call get_budget_status/get_goal_progress when you need fresher numbers):",
        "Current date: %s (%s). Currency: CNY (¥)."
        % (today.isoformat(), today.strftime("%A")),
        "Profile: risk_level=%s, safety_threshold=%s, monthly_income=%s, ui_language=%s."
        % (
            profile.get("risk_level", "moderate"),
            _money(threshold) if threshold is not None else "n/a",
            _money(profile.get("monthly_income")),
            profile.get("language", "en"),
        ),
    ]

    weekly = ledger.compute_budget_status(db, "weekly")
    monthly = ledger.compute_budget_status(db, "monthly")
    for period, status in (("Weekly", weekly), ("Monthly", monthly)):
        tiers = status["tiers"]
        spent = ", ".join(
            "%s %s of %s (%s%%)"
            % (
                tier,
                _money(tiers[tier]["spent"]),
                _money(tiers[tier]["limit"]),
                format(tiers[tier]["pct"], ".1f"),
            )
            for tier in ("survival", "buffer", "learning")
        )
        lines.append(
            "%s budget %s → %s: %s."
            % (period, status["period_start"], status["period_end"], spent)
        )
        lines.append(
            "%s totals: income %s, expense %s, net %s, locked for goals %s, "
            "spendable %s."
            % (
                period,
                _money(status["income_total"]),
                _money(status["expense_total"]),
                _money(status["net_balance"]),
                _money(status["locked_amount"]),
                _money(status["available_balance"]),
            )
        )

    if threshold is not None:
        below = agent.exceeds_safety_threshold(profile, weekly)
        lines.append(
            "Spendable money is %s the safety threshold."
            % ("BELOW" if below else "at or above")
        )

    goals = [agent.compute_goal_progress(goal) for goal in ledger.list_goals(db)]
    if not goals:
        lines.append("Savings goals: none yet.")
    else:
        described = []
        for goal in goals[:MAX_GOALS_IN_CONTEXT]:
            timing = ""
            if goal.get("deadline"):
                days_left = goal.get("days_left")
                if days_left is None:
                    timing = ", due %s" % goal["deadline"]
                elif days_left < 0:
                    timing = ", deadline passed %d days ago" % -days_left
                else:
                    timing = ", due %s (%d days left)" % (goal["deadline"], days_left)
            described.append(
                '"%s" %s%% funded (%s of %s%s)'
                % (
                    goal.get("name", ""),
                    format(float(goal.get("pct") or 0.0), ".1f"),
                    _money(goal.get("current_amount")),
                    _money(goal.get("target_amount")),
                    timing,
                )
            )
        extra = len(goals) - MAX_GOALS_IN_CONTEXT
        suffix = " (+%d more not shown)" % extra if extra > 0 else ""
        lines.append("Savings goals: %s%s." % ("; ".join(described), suffix))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool execution (local, deterministic)
# ---------------------------------------------------------------------------
def _execute_tool(db, name, tool_input):
    """Run one tool call locally; only `ledger` may touch the database."""
    if not isinstance(tool_input, dict):
        raise ValueError("tool input must be an object")

    if name == "add_transaction":
        try:
            amount = ledger.parse_number(tool_input.get("amount"))
        except ValueError:
            raise ValueError("amount must be positive") from None
        if amount <= 0:
            raise ValueError("amount must be positive")

        txn_type = tool_input.get("type")
        if txn_type not in ("income", "expense"):
            raise ValueError("type must be income or expense")

        category = tool_input.get("category")
        if category is None or category == "":
            category = "other"
        if not isinstance(category, str):
            raise ValueError("category must be a string")
        category = category.strip().lower()
        if not category:
            raise ValueError("category is required")
        if ledger.storable_text(category) is None:
            raise ValueError("category must be a string")
        if len(category) > limits.MAX_CATEGORY_LENGTH:
            raise ValueError("category is too long")

        note = tool_input.get("note")
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        if ledger.storable_text(note) is None:
            raise ValueError("note must be a string")
        if len(note) > limits.MAX_NOTE_LENGTH:
            raise ValueError("note is too long")

        return {
            "proposed_transaction": {
                "amount": amount,
                "type": txn_type,
                "category": category,
                "note": note,
            }
        }

    if name == "get_budget_status":
        period = tool_input.get("period")
        if period is None:
            period = "weekly"
        if period not in ("weekly", "monthly"):
            raise ValueError("period must be weekly or monthly")
        return ledger.compute_budget_status(db, period)

    if name == "get_goal_progress":
        name_filter = tool_input.get("name")
        if name_filter is None:
            name_filter = ""
        if not isinstance(name_filter, str):
            raise ValueError("name must be a string")
        name_filter = name_filter.strip().lower()
        goals = [agent.compute_goal_progress(g) for g in ledger.list_goals(db)]
        if name_filter:
            goals = [g for g in goals if name_filter in (g.get("name") or "").lower()]
        return {"goals": goals}

    return {"error": "unknown tool " + name}


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------
def _http_error_message(status, body):
    detail = ""
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("type") or "")
        elif error:
            detail = str(error)
        if not detail:
            detail = str(parsed.get("message") or "")
    if not detail:
        detail = " ".join((body or "").split())[:300]
    hint = _HTTP_HINTS.get(status, "unexpected response")
    return "AI provider error %s (%s)%s" % (
        status,
        hint,
        ": " + detail if detail else "",
    )


def _read_error_body(exc):
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 — a broken error body must not mask the status code
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw or "")


def _backoff(attempt):
    return min(0.5 * (2 ** attempt), 4.0)


def _retry_delay(exc, attempt):
    retry_after = exc.headers.get("retry-after") if exc.headers else None
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        delay = _backoff(attempt)
    return max(0.0, min(delay, 5.0))


def _post_json(url, payload, api_key, timeout, max_retries=0):
    """POST a JSON body and return the decoded response.

    Raises `LLMError` for anything the caller can fall back from: transport errors, HTTP
    errors (with a readable message that never contains the key), and invalid JSON.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + (api_key or ""),
        "User-Agent": "CampusWallet/1.0",
    }
    attempts = max(1, int(max_retries) + 1)

    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            if exc.code in _RETRY_STATUSES and attempt + 1 < attempts:
                time.sleep(_retry_delay(exc, attempt))
                continue
            raise LLMError(_http_error_message(exc.code, detail)) from None
        except urllib.error.URLError as exc:
            if attempt + 1 < attempts:
                time.sleep(_backoff(attempt))
                continue
            raise LLMError(
                "could not reach the AI provider: %s" % (exc.reason,)
            ) from None
        except TimeoutError:
            if attempt + 1 < attempts:
                time.sleep(_backoff(attempt))
                continue
            raise LLMError("the AI provider timed out after %ss" % timeout) from None
        except OSError as exc:
            raise LLMError("network error talking to the AI provider: %s" % exc) from None

        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise LLMError("the AI provider returned invalid JSON") from None

    raise LLMError("the AI provider did not answer")


def _log_usage(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    logger.debug(
        "LLM usage: prompt=%s completion=%s total=%s cached=%s",
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
        cached,
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class DeepSeekChat:
    """OpenAI-compatible ``/chat/completions`` client with function calling.

    DeepSeek, and any gateway that speaks the same format, is configured with
    `AI_BASE_URL` + `AI_MODEL`. The wire format is what the DeepSeek docs show:

    * tools are ``{"type": "function", "function": {name, description, parameters}}``
    * the model answers with ``message.tool_calls[]`` (``function.arguments`` is a JSON
      *string*), and the app replies with one ``{"role": "tool", "tool_call_id": ...}``
      message per call
    * ``thinking`` mode is a top-level ``{"type": "enabled" | "disabled"}`` switch; it
      rejects ``temperature`` and requires every ``reasoning_content`` to be echoed back
      while ``tools`` are present. It is only sent to DeepSeek's own host by default,
      because third-party gateways may not know the field.

    Well-known DeepSeek model spellings (``deepseek-v4.1-flash``, ``deepseek-v41-flash``…)
    are canonicalised by `config.resolve_model` when the endpoint is api.deepseek.com.
    """

    name = config.PROVIDER_DEEPSEEK

    def __init__(self, api_key=None, base_url=None, model=None, timeout=None,
                 max_tokens=None, temperature=None, thinking=None,
                 reasoning_effort=None, max_retries=None):
        self.api_key = config.AI_API_KEY if api_key is None else api_key
        self.base_url = (config.AI_BASE_URL if base_url is None else base_url).rstrip("/")
        requested_model = config.AI_MODEL if model is None else model
        self.model = config.resolve_model(requested_model, self.base_url)
        self.timeout = (
            (config.AI_TIMEOUT if timeout is None else timeout) or 30.0
        )
        self.max_tokens = config.AI_MAX_TOKENS if max_tokens is None else max_tokens
        self.temperature = config.AI_TEMPERATURE if temperature is None else temperature
        self.thinking = config.AI_THINKING if thinking is None else thinking
        self.reasoning_effort = (
            config.AI_REASONING_EFFORT if reasoning_effort is None else reasoning_effort
        )
        self.max_retries = config.AI_MAX_RETRIES if max_retries is None else max_retries

    @property
    def endpoint(self):
        return self.base_url + "/chat/completions"

    @property
    def thinking_mode(self):
        """``enabled``, ``disabled`` or ``omitted`` for this endpoint."""
        return config.resolve_thinking(self.thinking, self.base_url)

    @property
    def thinking_enabled(self):
        return self.thinking_mode == "enabled"

    def output_budget(self):
        """`max_tokens` also caps the reasoning tokens, so thinking mode needs headroom."""
        if self.thinking_enabled:
            return max(int(self.max_tokens), 2048)
        return int(self.max_tokens)

    def payload(self, system_prompt, messages):
        mode = self.thinking_mode
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + list(messages),
            "max_tokens": self.output_budget(),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in TOOLS
            ],
            "stream": False,
        }
        if mode == "omitted":
            # Third-party OpenAI-compatible gateways may reject the DeepSeek-only field.
            body["temperature"] = self.temperature
            return body
        body["thinking"] = {"type": mode}
        if mode == "enabled":
            if self.reasoning_effort:
                body["reasoning_effort"] = self.reasoning_effort
        else:
            # Thinking mode ignores temperature; only send it when it actually applies.
            body["temperature"] = self.temperature
        return body

    def complete(self, system_prompt, messages):
        data = _post_json(
            self.endpoint,
            self.payload(system_prompt, messages),
            self.api_key,
            self.timeout,
            self.max_retries,
        )
        return self.parse(data)

    def parse(self, data):
        """Normalise a ``chat.completion`` response into a `Turn`."""
        if not isinstance(data, dict):
            raise LLMError("the AI provider returned an unexpected response")
        if data.get("error"):
            error = data["error"]
            detail = error.get("message") if isinstance(error, dict) else error
            raise LLMError("the AI provider returned an error: %s" % (detail or error))

        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise LLMError("the AI provider returned no choices")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            message = {}
        _log_usage(data)

        calls = []
        for index, item in enumerate(message.get("tool_calls") or []):
            if isinstance(item, dict):
                calls.append(_parse_openai_tool_call(item, index))

        return Turn(
            text=str(message.get("content") or "").strip(),
            tool_calls=calls,
            raw=message,
            finish_reason=str(choice.get("finish_reason") or ""),
        )

    def append_assistant(self, messages, turn):
        raw = turn.raw if isinstance(turn.raw, dict) else {}
        message = {"role": "assistant", "content": raw.get("content")}
        if raw.get("tool_calls"):
            message["tool_calls"] = raw["tool_calls"]
        # Required while `tools` is present in thinking mode; harmless otherwise.
        if raw.get("reasoning_content"):
            message["reasoning_content"] = raw["reasoning_content"]
        messages.append(message)

    def append_tool_results(self, messages, results):
        for call, result, _is_error in results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )


def _parse_openai_tool_call(item, index):
    function = item.get("function")
    if not isinstance(function, dict):
        function = {}
    raw_arguments = function.get("arguments")
    arguments = None
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
        raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
    else:
        raw_arguments = raw_arguments if isinstance(raw_arguments, str) else ""
        try:
            parsed = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            arguments = parsed
    return ToolCall(
        id=str(item.get("id") or "call_%d" % index),
        name=str(function.get("name") or ""),
        arguments=arguments,
        raw_arguments=raw_arguments,
    )


class AnthropicChat:
    """Claude Messages API client (optional backend).

    The pre-DeepSeek integration is preserved deliberately: a lone `ANTHROPIC_API_KEY` (or
    `AI_API_KEY`) still selects this backend, the model default is still `claude-sonnet-5`,
    the output cap still defaults to 400 tokens, and the SDK keeps its own timeout unless
    `AI_TIMEOUT` is set. `AI_BASE_URL` is only forwarded when it differs from the default,
    so an existing Claude deployment issues the same SDK call as before.
    """

    name = config.PROVIDER_ANTHROPIC

    def __init__(self, api_key=None, model=None, timeout=None, max_tokens=None,
                 base_url=None):
        self.api_key = config.AI_API_KEY if api_key is None else api_key
        self.base_url = (config.AI_BASE_URL if base_url is None else base_url).rstrip("/")
        requested_model = config.AI_MODEL if model is None else model
        self.model = config.resolve_model(requested_model, self.base_url)
        self.timeout = config.AI_TIMEOUT if timeout is None else timeout
        self.max_tokens = config.AI_MAX_TOKENS if max_tokens is None else max_tokens

    def complete(self, system_prompt, messages):
        try:
            import anthropic  # imported lazily: only needed for this backend
        except ImportError as exc:
            raise LLMError(
                "the anthropic package is not installed (pip install anthropic)"
            ) from exc

        options = {"api_key": self.api_key}
        if self.base_url and self.base_url != config.DEFAULT_BASE_URLS[
            config.PROVIDER_ANTHROPIC
        ]:
            options["base_url"] = self.base_url
        if self.timeout is not None:
            options["timeout"] = self.timeout
        client = anthropic.Anthropic(**options)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                tools=[
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "input_schema": tool["parameters"],
                    }
                    for tool in TOOLS
                ],
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — the SDK raises a wide range of errors
            raise LLMError("Anthropic API error: %s" % exc) from exc

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        calls = [
            ToolCall(id=block.id, name=block.name, arguments=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return Turn(
            text=text,
            tool_calls=calls,
            raw=response.content,
            finish_reason=str(response.stop_reason or ""),
        )

    def append_assistant(self, messages, turn):
        messages.append({"role": "assistant", "content": turn.raw})

    def append_tool_results(self, messages, results):
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                        "is_error": bool(is_error),
                    }
                    for call, result, is_error in results
                ],
            }
        )


def build_provider():
    """Instantiate the configured backend, or raise `LLMError` when it cannot be used."""
    provider = config.normalize_provider(config.AI_PROVIDER)
    if provider is None:
        raise LLMError("unsupported AI_PROVIDER: %r" % (config.AI_PROVIDER,))
    if not config.looks_like_key(config.AI_API_KEY):
        raise LLMError("no API key configured for %s" % provider)
    if provider == config.PROVIDER_ANTHROPIC:
        return AnthropicChat()
    return DeepSeekChat()


# ---------------------------------------------------------------------------
# Shared tool loop
# ---------------------------------------------------------------------------
def _run_tool_loop(db, provider, system_prompt, messages):
    """Ask, execute any tool calls locally, feed the results back, repeat.

    Returns ``(reply_text, proposal_or_None, finish_reason)``. The proposal is the last
    `add_transaction` result: app.py turns it into a confirmation card, which is the only
    path to `ledger.add_transaction`.
    """
    proposal = None
    text = ""
    finish_reason = ""

    for _ in range(MAX_TOOL_ROUNDS):
        turn = provider.complete(system_prompt, messages)
        finish_reason = turn.finish_reason or finish_reason
        if turn.text:
            text = turn.text
        if not turn.tool_calls:
            return text, proposal, finish_reason

        provider.append_assistant(messages, turn)

        results = []
        for call in turn.tool_calls:
            try:
                result = _execute_tool(db, call.name, call.arguments)
                is_error = False
            except ValueError as exc:
                result = {"error": str(exc)}
                is_error = True
            if call.name == "add_transaction" and not is_error:
                proposal = result.get("proposed_transaction") or proposal
            results.append((call, result, is_error))
        provider.append_tool_results(messages, results)

    logger.debug("tool loop hit MAX_TOOL_ROUNDS with finish_reason=%r", finish_reason)
    return text, proposal, finish_reason


def chat_with_llm(db, message, history=None):
    """Run one chat turn against the configured backend.

    Returns ``(reply_text, transaction_proposal_or_None)``. Raises `LLMError` when the
    backend cannot answer so the caller can fall back to the rule-based parser.
    """
    provider = build_provider()
    messages = build_messages(message, history)
    system_prompt = SYSTEM_PROMPT
    if config.AI_CONTEXT:
        try:
            snapshot = build_context_snapshot(db)
        except Exception:  # noqa: BLE001 — context is a bonus, never a reason to fail a chat
            logger.debug("could not build the budget context snapshot", exc_info=True)
        else:
            if snapshot:
                system_prompt = SYSTEM_PROMPT + "\n\n" + snapshot

    text, proposal, finish_reason = _run_tool_loop(
        db, provider, system_prompt, messages
    )
    text = (text or "").strip()
    if not text and proposal is None:
        # An empty reply (for example one truncated by max_tokens) is a failure, not an
        # answer: the caller falls back instead of rendering an empty chat bubble.
        raise LLMError(
            "the model returned an empty reply (finish_reason=%s)"
            % (finish_reason or "unknown",)
        )
    return text, proposal
