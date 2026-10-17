"""Optional LLM-powered chat layer.

Wraps the Anthropic Messages API with tool use so the model can never touch financial
data directly. The model only ever describes *what* it wants via a tool call; every tool
is executed locally against the deterministic `ledger`/`agent` modules, which enforce the
actual rules (see ledger.add_transaction). If no API key is configured, or the API call
fails for any reason, this module raises — callers are expected to catch that and fall
back to the rule-based parser in agent.py, so the app keeps working without a key.
"""
import json

import agent
import config
import ledger
import limits

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

TOOLS = [
    {
        "name": "add_transaction",
        "description": (
            "Prepare a transaction proposal (expense or income) for the application to "
            "validate. This tool never writes to the ledger; the application applies "
            "confirmation and safety rules before calling the canonical ledger operation."
        ),
        "input_schema": {
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
        "input_schema": {
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
        "input_schema": {
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

# Values shipped in .env.example (or obvious dummies) must not look like a configured key:
# otherwise every chat makes a doomed API call before falling back to the rule parser.
_PLACEHOLDER_KEYS = {
    "",
    "enter ai api",
    "your api key",
    "your-api-key",
    "changeme",
    "change-me",
    "api-key",
    "sk-...",
}


def is_configured():
    key = (config.AI_API_KEY or "").strip()
    return bool(key) and key.lower() not in _PLACEHOLDER_KEYS


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
    """Anthropic-style messages: recent text turns + the current user message.

    The Messages API expects alternating roles, so consecutive turns are merged and a
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


def chat_with_llm(db, message, history=None):
    """Runs the tool-use loop. Returns (reply_text, transaction_proposal_or_None)."""
    import anthropic  # imported lazily so the package is only required once a key is set

    client = anthropic.Anthropic(api_key=config.AI_API_KEY)
    messages = build_messages(message, history)
    proposal = None
    text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

        if response.stop_reason != "tool_use":
            return text, proposal

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result = _execute_tool(db, block.name, block.input)
                is_error = False
            except ValueError as exc:
                result = {"error": str(exc)}
                is_error = True

            if block.name == "add_transaction" and not is_error:
                proposal = result.get("proposed_transaction")

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    return text, proposal
