"""Flask REST API for the college-student financial planning AI agent demo."""
import json
import logging
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta

from flask import Flask, g, jsonify, request, render_template

import agent
import ledger
import limits
import llm_agent

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "economical_agent.db")
DEFAULT_USER_ID = ledger.DEFAULT_USER_ID

# sqlite3 binds only signed 64-bit integers. Path/query parameters accept arbitrary
# Python ints, so an out-of-range id used to surface as a 500 (OverflowError) instead
# of a clean 404. Clamp every user-supplied integer before it reaches the driver.
SQLITE_INTEGER_MIN = -(2 ** 63)
SQLITE_INTEGER_MAX = 2 ** 63 - 1
MAX_LIST_LIMIT = 1000

app = Flask(__name__)
logger = logging.getLogger(__name__)


def _fits_sqlite_integer(value):
    return SQLITE_INTEGER_MIN <= value <= SQLITE_INTEGER_MAX


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _warn_if_not_writable(conn):
    """Surface an unwritable database at startup instead of one HTTP 500 per request.

    On Windows this happens when the file was created before a workspace/tool sandbox
    granted write access: SQLite can read it but cannot create its journal. The chat then
    dies halfway through ("attempt to write a readonly database"). A clear startup error
    beats a confusing failure during a conversation; the fix is in README-CN.md.
    """
    try:
        # BEGIN alone does not touch the file: perform a real (rolled back) row write so a
        # read-only file is detected here, at startup, instead of during a chat request.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE UserProfile SET name = name WHERE id = ?", (DEFAULT_USER_ID,)
        )
        conn.rollback()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "readonly" in message:
            logger.error(
                "Database %s is READ-ONLY for this process: chat confirmations, "
                "transactions and budget edits will fail. Replace it with a fresh copy "
                "created inside this folder, or run the app from a normal terminal. "
                "See README-CN.md (Windows troubleshooting).",
                DB_PATH,
            )
        elif "locked" in message:
            logger.warning(
                "Database %s is locked by another process; writes will fail until it exits.",
                DB_PATH,
            )


def init_db():
    """Create the schema and the demo profile. Safe to call repeatedly."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS UserProfile (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT 'Student',
            risk_level TEXT NOT NULL DEFAULT 'moderate',
            security_threshold REAL NOT NULL DEFAULT 50.0,
            monthly_income REAL NOT NULL DEFAULT 0,
            language TEXT NOT NULL DEFAULT 'en',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS "Transaction" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
            category TEXT NOT NULL,
            tier TEXT NOT NULL,
            note TEXT,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES UserProfile(id)
        );

        CREATE TABLE IF NOT EXISTS BudgetPlan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            period_type TEXT NOT NULL CHECK(period_type IN ('weekly', 'monthly')),
            period_start TEXT NOT NULL,
            survival_limit REAL NOT NULL,
            buffer_limit REAL NOT NULL,
            learning_limit REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, period_type, period_start),
            FOREIGN KEY (user_id) REFERENCES UserProfile(id)
        );

        CREATE TABLE IF NOT EXISTS Goal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            target_amount REAL NOT NULL,
            current_amount REAL NOT NULL DEFAULT 0,
            deadline TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES UserProfile(id)
        );

        CREATE TABLE IF NOT EXISTS ChatAction (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL CHECK(action_type IN ('confirm_credit', 'risk_warning')),
            payload TEXT NOT NULL,
            period_type TEXT NOT NULL CHECK(period_type IN ('weekly', 'monthly')),
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'consumed', 'cancelled', 'expired')),
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES UserProfile(id)
        );
        """
    )
    row = cur.execute(
        "SELECT id FROM UserProfile WHERE id = ?", (DEFAULT_USER_ID,)
    ).fetchone()
    if row is None:
        try:
            cur.execute(
                "INSERT INTO UserProfile (id, created_at) VALUES (?, ?)",
                (DEFAULT_USER_ID, ledger.now().isoformat()),
            )
        except sqlite3.OperationalError:
            pass
    _warn_if_not_writable(conn)
    conn.commit()
    conn.close()


def row_to_dict(row):
    return dict(row)


def get_profile_row(db):
    return ledger.get_profile(db)


def _json_body(force=True):
    """Return the request body when it is a JSON object, else None (the route answers 400).

    ``force=True`` keeps the documented tolerance for a client that forgets the
    ``Content-Type`` header. A body nested past the decoder's recursion limit raises
    RecursionError, which ``silent=True`` does not swallow — bad input, not a server
    fault — so it maps to the same 400 as malformed JSON.
    """
    try:
        data = request.get_json(force=force, silent=True)
    except RecursionError:
        return None
    return data if isinstance(data, dict) else None


def _storable_name(value):
    """A user-facing name: non-empty, trimmed, and actually encodable as UTF-8.

    SQLite rejects a lone surrogate (``"\\ud800"``) while binding it, which used to
    surface as a 500 from the goal routes.
    """
    if not isinstance(value, str):
        raise ValueError("name must be a string")
    name = value.strip()
    if not name:
        raise ValueError("name must not be empty")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("name must be valid UTF-8 text") from None
    if len(name) > limits.MAX_NAME_LENGTH:
        raise ValueError("name is too long")
    return name


def _nonnegative(value, field):
    """A finite JSON number that may be zero (money limits and balances)."""
    try:
        number = ledger.parse_number(value)
    except ValueError:
        raise ValueError(f"{field} must be a number") from None
    if number < 0:
        raise ValueError(f"{field} must be nonnegative")
    return number


def _positive(value, field):
    """A finite JSON number greater than zero (a goal target)."""
    number = _nonnegative(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _parse_deadline(value):
    """Validate an optional goal deadline and return it in canonical ``YYYY-MM-DD`` form.

    ``null``/blank means "no deadline". Every reader parses the column with
    ``date.fromisoformat()``, so anything else is rejected here instead of being
    persisted and silently losing the goal's countdown and weekly-savings insight.
    Re-checking against ``isoformat()`` keeps exactly one spelling in the database.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("deadline must be a string in YYYY-MM-DD format")
    value = value.strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("deadline must be a valid date in YYYY-MM-DD format") from None
    if parsed.isoformat() != value:
        raise ValueError("deadline must be a valid date in YYYY-MM-DD format")
    return parsed.isoformat()


def _create_chat_action(db, action_type, payload, period_type):
    now = ledger.now()
    action_id = uuid.uuid4().hex
    expires_at = (now + timedelta(minutes=10)).isoformat()
    db.execute(
        "INSERT INTO ChatAction (id, user_id, action_type, payload, period_type, "
        "expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            action_id,
            DEFAULT_USER_ID,
            action_type,
            json.dumps(payload),
            period_type,
            expires_at,
            now.isoformat(),
        ),
    )
    db.commit()
    return {
        "id": action_id,
        "type": action_type,
        "payload": payload,
        "expires_at": expires_at,
    }


def _action_response(action, text=None, key=None, params=None):
    response = {"action": action}
    if text is not None:
        response["text"] = text
    else:
        response["key"] = key
        response["params"] = params or {}
    return response


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/profile", methods=["GET"])
def get_profile():
    db = get_db()
    return jsonify(get_profile_row(db))


@app.route("/api/profile", methods=["PUT"])
def update_profile():
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400

    db = get_db()
    profile = get_profile_row(db)

    name = profile["name"]
    if "name" in data:
        try:
            name = _storable_name(data["name"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    risk_level = data.get("risk_level", profile["risk_level"])
    if risk_level not in ("conservative", "moderate", "aggressive"):
        return jsonify(
            {"error": "risk_level must be conservative, moderate, or aggressive"}
        ), 400

    language = data.get("language", profile["language"])
    if language not in ("en", "zh"):
        return jsonify({"error": "language must be en or zh"}), 400

    try:
        security_threshold = _nonnegative(
            data.get("security_threshold", profile["security_threshold"]),
            "security_threshold",
        )
        monthly_income = _nonnegative(
            data.get("monthly_income", profile["monthly_income"]), "monthly_income"
        )
    except ValueError:
        return jsonify(
            {"error": "security_threshold and monthly_income must be numbers"}
        ), 400

    db.execute(
        "UPDATE UserProfile SET name = ?, risk_level = ?, security_threshold = ?, "
        "monthly_income = ?, language = ? WHERE id = ?",
        (
            name,
            risk_level,
            security_threshold,
            monthly_income,
            language,
            DEFAULT_USER_ID,
        ),
    )
    db.commit()
    return jsonify(get_profile_row(db))


@app.route("/api/transactions", methods=["GET"])
def list_transactions():
    db = get_db()
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    rows = db.execute(
        'SELECT * FROM "Transaction" WHERE user_id = ? '
        "ORDER BY occurred_at DESC, id DESC LIMIT ?",
        (DEFAULT_USER_ID, limit),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/transactions", methods=["POST"])
def create_transaction():
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400
    try:
        amount = data["amount"]
        txn_type = data["type"]
        category = data["category"]
    except (KeyError, TypeError):
        return jsonify({"error": "amount, type, and category are required"}), 400

    db = get_db()
    try:
        result = ledger.add_transaction(
            db,
            amount,
            txn_type,
            category,
            data.get("note", ""),
            data.get("occurred_at"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result["transaction"]), 201


@app.route("/api/transactions/<int:txn_id>", methods=["DELETE"])
def delete_transaction(txn_id):
    if not _fits_sqlite_integer(txn_id):
        return jsonify({"error": "transaction not found"}), 404
    db = get_db()
    cur = db.execute(
        'DELETE FROM "Transaction" WHERE id = ? AND user_id = ?',
        (txn_id, DEFAULT_USER_ID),
    )
    db.commit()
    if cur.rowcount != 1:
        return jsonify({"error": "transaction not found"}), 404
    return jsonify({"deleted": txn_id})


@app.route("/api/ledger/add", methods=["POST"])
def ledger_add():
    """Structured accounting interface —the canonical non-chat way to add a transaction.
    Goes through ledger.add_transaction, the same deterministic rules engine the chat
    assistant (both LLM and rule-based) uses, so the response includes the fresh budget
    status (with locked/available balance) alongside the transaction that was recorded.
    """
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400
    try:
        amount = data["amount"]
        txn_type = data["type"]
        category = data["category"]
    except (KeyError, TypeError):
        return jsonify({"error": "amount, type, and category are required"}), 400

    db = get_db()
    try:
        result = ledger.add_transaction(
            db,
            amount,
            txn_type,
            category,
            data.get("note", ""),
            data.get("occurred_at", None),
            data.get("period_type", "weekly"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
@app.route("/api/budget", methods=["GET"])
def get_budget():
    period_type = request.args.get("period", "weekly")
    if period_type not in ("weekly", "monthly"):
        return jsonify({"error": "period must be weekly or monthly"}), 400
    db = get_db()
    return jsonify(ledger.get_or_create_budget_plan(db, period_type))


@app.route("/api/budget", methods=["PUT"])
def update_budget():
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400

    period_type = data.get("period_type", "weekly")
    if period_type not in ("weekly", "monthly"):
        return jsonify({"error": "period_type must be weekly or monthly"}), 400

    # Validate before touching the database: a rejected request must not create a plan.
    limits = {}
    try:
        for field in ("survival_limit", "buffer_limit", "learning_limit"):
            if field in data:
                limits[field] = _nonnegative(data[field], field)
    except ValueError:
        return jsonify({"error": "limits must be numbers"}), 400

    db = get_db()
    plan = ledger.get_or_create_budget_plan(db, period_type)
    survival_limit = limits.get("survival_limit", plan["survival_limit"])
    buffer_limit = limits.get("buffer_limit", plan["buffer_limit"])
    learning_limit = limits.get("learning_limit", plan["learning_limit"])

    updated = ledger.update_budget_plan(
        db, period_type, survival_limit, buffer_limit, learning_limit
    )
    return jsonify(updated)


@app.route("/api/budget/status", methods=["GET"])
def budget_status():
    period_type = request.args.get("period", "weekly")
    if period_type not in ("weekly", "monthly"):
        return jsonify({"error": "period must be weekly or monthly"}), 400
    db = get_db()
    return jsonify(ledger.compute_budget_status(db, period_type))


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
@app.route("/api/goals", methods=["GET"])
def list_goals():
    db = get_db()
    return jsonify(ledger.list_goals(db))


@app.route("/api/goals", methods=["POST"])
def create_goal():
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or "target_amount" not in data:
        return jsonify({"error": "name and target_amount are required"}), 400
    try:
        name = _storable_name(name)
        target_amount = _positive(data["target_amount"], "target_amount")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        current_amount = _nonnegative(
            data.get("current_amount", 0), "current_amount"
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        deadline = _parse_deadline(data.get("deadline"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO Goal (user_id, name, target_amount, current_amount, deadline, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            DEFAULT_USER_ID,
            name.strip(),
            target_amount,
            current_amount,
            deadline,
            ledger.now().isoformat(),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM Goal WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.route("/api/goals/<int:goal_id>", methods=["PUT"])
def update_goal(goal_id):
    if not _fits_sqlite_integer(goal_id):
        return jsonify({"error": "goal not found"}), 404
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400

    db = get_db()
    goal = db.execute(
        "SELECT * FROM Goal WHERE id = ? AND user_id = ?", (goal_id, DEFAULT_USER_ID)
    ).fetchone()
    if goal is None:
        return jsonify({"error": "goal not found"}), 404
    goal = row_to_dict(goal)

    name = goal["name"]
    target_amount = goal["target_amount"]
    current_amount = goal["current_amount"]
    deadline = goal["deadline"]
    try:
        if "name" in data:
            name = _storable_name(data["name"])
        if "target_amount" in data:
            target_amount = _positive(data["target_amount"], "target_amount")
        if "current_amount" in data:
            current_amount = _nonnegative(data["current_amount"], "current_amount")
        if "deadline" in data:
            deadline = _parse_deadline(data["deadline"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db.execute(
        "UPDATE Goal SET name = ?, target_amount = ?, current_amount = ?, deadline = ? "
        "WHERE id = ?",
        (name.strip(), target_amount, current_amount, deadline, goal_id),
    )
    db.commit()
    row = db.execute("SELECT * FROM Goal WHERE id = ?", (goal_id,)).fetchone()
    return jsonify(row_to_dict(row))


@app.route("/api/goals/<int:goal_id>", methods=["DELETE"])
def delete_goal(goal_id):
    if not _fits_sqlite_integer(goal_id):
        return jsonify({"error": "goal not found"}), 404
    db = get_db()
    cur = db.execute(
        "DELETE FROM Goal WHERE id = ? AND user_id = ?", (goal_id, DEFAULT_USER_ID)
    )
    db.commit()
    if cur.rowcount != 1:
        return jsonify({"error": "goal not found"}), 404
    return jsonify({"deleted": goal_id})


@app.route("/api/goal/progress", methods=["GET"])
def goal_progress():
    db = get_db()
    goals = [agent.compute_goal_progress(g) for g in ledger.list_goals(db)]
    raw_goal_id = request.args.get("id")
    if raw_goal_id is None:
        return jsonify(goals)
    try:
        goal_id = int(raw_goal_id.strip())
    except (AttributeError, TypeError, ValueError):
        return jsonify({"error": "id must be an integer"}), 400
    if not _fits_sqlite_integer(goal_id):
        return jsonify({"error": "goal not found"}), 404
    match = next((g for g in goals if g["id"] == goal_id), None)
    if match is None:
        return jsonify({"error": "goal not found"}), 404
    return jsonify(match)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
@app.route("/api/insights", methods=["GET"])
def insights():
    period_type = request.args.get("period", "weekly")
    if period_type not in ("weekly", "monthly"):
        return jsonify({"error": "period must be weekly or monthly"}), 400
    db = get_db()
    profile = get_profile_row(db)
    status = ledger.compute_budget_status(db, period_type)
    goals = ledger.list_goals(db)
    return jsonify(agent.build_insights(profile, status, goals))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def _goal_progress_params(progress):
    days_left = progress["days_left"]
    return {
        "name": progress["name"],
        "current": progress["current_amount"],
        "target": progress["target_amount"],
        "pct": progress["pct"],
        # A past deadline is reported as overdue days; "days left: -46" must never reach the UI.
        "days_left": max(days_left, 0) if days_left is not None else None,
        "days_overdue": max(-days_left, 0) if days_left is not None else 0,
    }


def _pending_transaction_action(db, payload):
    """Create the confirmation card for a proposed transaction.

    The risk flag comes from `agent.exceeds_safety_threshold`, the same rule the
    insights feed uses, so the dashboard warning and the chat warning always agree.
    """
    profile = get_profile_row(db)
    status = ledger.compute_budget_status(db, "weekly")
    needs_warning = payload["type"] == "expense" and agent.exceeds_safety_threshold(
        profile, status, payload["amount"]
    )
    action_type = "risk_warning" if needs_warning else "confirm_credit"
    return _create_chat_action(db, action_type, payload, "weekly")


def _chat_with_rules(db, message):
    """Deterministic fallback used when no LLM is configured, or the LLM call fails."""
    parsed = agent.parse_chat_message(message)
    intent = parsed.get("intent")

    if intent == "add_transaction":
        try:
            amount = ledger.parse_number(parsed.get("amount"))
        except ValueError:
            # Missing, non-numeric or non-finite (e.g. "9" * 400 parses to inf): ask for an amount.
            return {"key": "chat_missing_amount", "params": {}}
        if amount <= 0:
            return {"key": "chat_missing_amount", "params": {}}

        txn_type = parsed.get("txn_type") or "expense"
        category = parsed.get("category") or ("income" if txn_type == "income" else "other")
        payload = {
            "amount": amount,
            "type": txn_type,
            "category": category,
            "note": "",
        }

        action = _pending_transaction_action(db, payload)
        return _action_response(action, None, "chat_action_preview", None)

    if intent == "budget_status":
        status = ledger.compute_budget_status(db, "weekly")
        return _action_response(
            None,
            None,
            "chat_budget_status",
            {
                "net_balance": status["net_balance"],
                "available_balance": status["available_balance"],
                "survival_remaining": status["tiers"]["survival"]["remaining"],
                "buffer_remaining": status["tiers"]["buffer"]["remaining"],
                "learning_remaining": status["tiers"]["learning"]["remaining"],
            },
        )

    if intent == "goal_progress":
        goals = [agent.compute_goal_progress(g) for g in ledger.list_goals(db)]
        if not goals:
            return {"key": "chat_no_goals", "params": {}}

        goal_name = parsed.get("goal_name")
        if goal_name:
            match = next(
                (g for g in goals if goal_name.lower() in (g["name"] or "").lower()),
                None,
            )
            if match is None:
                return {"key": "chat_goal_not_found", "params": {"name": goal_name}}
            return {
                "key": "chat_goal_progress",
                "params": _goal_progress_params(match),
            }

        if len(goals) == 1:
            return {
                "key": "chat_goal_progress",
                "params": _goal_progress_params(goals[0]),
            }

        return {
            "key": "chat_goals_summary",
            "params": {
                "count": len(goals),
                "names": ", ".join(g["name"] for g in goals),
            },
        }

    return {"key": "chat_help", "params": {}}


@app.route("/api/chat/status", methods=["GET"])
def chat_status():
    """Which chat backend `/api/chat` will use.

    Secret-free on purpose: the browser uses it to label the assistant (DeepSeek vs. the
    built-in rule engine) and it is safe to expose in the page.
    """
    return jsonify(llm_agent.provider_status())


@app.route("/api/chat", methods=["POST"])
def chat():
    """Receives natural-language text and returns the agent's response."""
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400
    message = data.get("message")
    if message is None:
        return jsonify({"error": "message is required"}), 400
    if not isinstance(message, str):
        return jsonify({"error": "message must be a string"}), 400
    message = message.strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    if len(message) > limits.MAX_MESSAGE_LENGTH:
        return jsonify({"error": "message is too long"}), 400

    # Recent turns are plain text only; llm_agent normalises/caps them before use.
    history = data.get("history")
    if history is not None and not isinstance(history, list):
        return jsonify({"error": "history must be an array"}), 400
    if isinstance(history, list) and len(history) > 50:
        history = history[-50:]

    db = get_db()
    if llm_agent.is_configured():
        try:
            text, txn = llm_agent.chat_with_llm(db, message, history)
            if txn is not None:
                payload = {
                    "amount": txn["amount"],
                    "type": txn["type"],
                    "category": txn["category"],
                    "note": txn.get("note", ""),
                }
                action = _pending_transaction_action(db, payload)
                # A model that proposes silently still gets a localized prompt on the card.
                response = _action_response(
                    action, text or None, "chat_action_preview", None
                )
                response["source"] = "llm"
                return jsonify(response)
            return jsonify({"text": text, "source": "llm"})
        except Exception as exc:  # noqa: BLE001 — any LLM failure falls back to the rules engine
            logger.warning(
                "LLM chat failed (%s: %s); falling back to the rule-based parser",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    response = _chat_with_rules(db, message)
    response["source"] = "rules"
    return jsonify(response)


@app.route("/api/chat/action", methods=["POST"])
def chat_action():
    data = _json_body()
    if data is None:
        return jsonify({"error": "a JSON object is required"}), 400

    decision = data.get("decision")
    if decision not in ("confirm", "cancel"):
        return jsonify({"error": "decision must be confirm or cancel"}), 400

    action_id = data.get("id")
    if not isinstance(action_id, str) or not action_id:
        return jsonify({"error": "action id is required"}), 400

    db = get_db()
    action = db.execute(
        "SELECT * FROM ChatAction WHERE id = ? AND user_id = ?",
        (action_id, DEFAULT_USER_ID),
    ).fetchone()
    if action is None:
        return jsonify({"error": "action not found"}), 404
    action = row_to_dict(action)

    if action["status"] != "pending":
        return jsonify({"error": "action already " + action["status"]}), 409

    try:
        expired = datetime.fromisoformat(action["expires_at"]) < ledger.now()
    except (TypeError, ValueError):
        expired = False
    if expired:
        db.execute("UPDATE ChatAction SET status = 'expired' WHERE id = ?", (action_id,))
        db.commit()
        return jsonify({"error": "action expired"}), 410

    if decision == "cancel":
        db.execute(
            "UPDATE ChatAction SET status = 'cancelled' WHERE id = ?", (action_id,)
        )
        db.commit()
        return jsonify({"status": "cancelled", "id": action_id})

    payload = json.loads(action["payload"])
    updated = db.execute(
        "UPDATE ChatAction SET status = 'consumed' WHERE id = ? AND status = 'pending'",
        (action_id,),
    )
    db.commit()
    if updated.rowcount != 1:
        return jsonify({"error": "action already consumed"}), 409

    try:
        ledger.add_transaction(
            db,
            payload["amount"],
            payload["type"],
            payload["category"],
            payload.get("note", ""),
            None,
            action["period_type"],
        )
    except ValueError as exc:
        db.execute(
            "UPDATE ChatAction SET status = 'cancelled' WHERE id = ?", (action_id,)
        )
        db.commit()
        return jsonify({"error": str(exc)}), 400

    return jsonify({"status": "confirmed", "id": action_id}), 201


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
