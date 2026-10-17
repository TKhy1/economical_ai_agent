"""Rule-based financial planning logic (the "AI agent" brain of the demo)."""
import re
from datetime import date

RISK_ALLOCATIONS = {
    "conservative": (0.60, 0.30, 0.10),  # survival, buffer, learning
    "moderate": (0.50, 0.25, 0.25),
    "aggressive": (0.40, 0.20, 0.40),
}

SURVIVAL_CATEGORIES = {"rent", "food", "groceries", "utilities", "transport", "tuition", "healthcare", "phone"}
LEARNING_CATEGORIES = {"books", "courses", "software", "career", "workshop"}
BUFFER_CATEGORIES = {"entertainment", "shopping", "subscriptions", "travel", "emergency", "other"}
INCOME_CATEGORIES = {"income", "allowance", "scholarship", "job", "parttime"}


def category_tier(category, txn_type):
    """Map a transaction to the fund-pool tier it is charged against.

    Income always lands in the `income` tier. Every expense has to land in one of
    the three spending pools, otherwise it would count in `expense_total` but be
    invisible in every pool: an income-style category on an expense (which can
    arrive through the API or an LLM proposal) therefore falls through to the
    catch-all `buffer` pool.
    """
    category = (category or "").strip().lower()
    if txn_type == "income":
        return "income"
    if category in SURVIVAL_CATEGORIES:
        return "survival"
    if category in LEARNING_CATEGORIES:
        return "learning"
    return "buffer"


def available_balance(budget_status):
    """Spendable money from a budget-status mapping (net balance minus locked goals).

    `ledger.compute_budget_status` always provides `available_balance`; the
    `net_balance` fallback only keeps partial callers (older tests, ad-hoc dicts)
    working.
    """
    value = budget_status.get("available_balance")
    if value is None:
        value = budget_status.get("net_balance", 0.0)
    return float(value or 0.0)


def available_after_spend(budget_status, amount):
    """Available balance left after paying `amount`; negative when overspending."""
    return round(available_balance(budget_status) - float(amount or 0.0), 2)


def exceeds_safety_threshold(profile, budget_status, amount=0.0):
    """True when spendable money (optionally after a proposed `amount`) is below the
    profile's safety threshold.

    This is the single definition of the rule: `build_insights` and the chat
    confirmation gate both call it, so the dashboard and the warning card can never
    disagree. The threshold applies to *available* money because goal money is
    already committed and cannot be spent.
    """
    return available_after_spend(budget_status, amount) < float(
        profile["security_threshold"]
    )


def generate_budget_plan(monthly_income, risk_level, period_type):
    survival_r, buffer_r, learning_r = RISK_ALLOCATIONS.get(risk_level, RISK_ALLOCATIONS["moderate"])
    base = float(monthly_income or 0)
    if period_type == "weekly":
        base = base / 4.0
    return {
        "survival_limit": round(base * survival_r, 2),
        "buffer_limit": round(base * buffer_r, 2),
        "learning_limit": round(base * learning_r, 2),
    }


def build_insights(profile, budget_status, goals):
    """Return insights as {level, key, params} so the frontend can render them in any language."""
    insights = []

    for tier in ("survival", "buffer", "learning"):
        pct = budget_status["tiers"][tier]["pct"]
        if pct >= 100:
            insights.append({
                "level": "danger",
                "key": "tier_exceeded",
                "params": {"tier": tier, "pct": round(pct)},
            })
        elif pct >= 80:
            insights.append({
                "level": "warning",
                "key": "tier_warning",
                "params": {"tier": tier, "pct": round(pct)},
            })

    if exceeds_safety_threshold(profile, budget_status):
        insights.append({
            "level": "danger",
            "key": "balance_below_threshold",
            "params": {
                "balance": round(available_balance(budget_status), 2),
                "threshold": round(profile["security_threshold"], 2),
            },
        })

    for g in goals:
        remaining = g["target_amount"] - g["current_amount"]
        if remaining <= 0:
            insights.append({"level": "success", "key": "goal_reached", "params": {"name": g["name"]}})
            continue
        if g["deadline"]:
            try:
                deadline = date.fromisoformat(g["deadline"])
            except ValueError:
                continue
            days_left = (deadline - date.today()).days
            if days_left >= 0:
                # A goal is still actionable on its deadline day: 0 days left means
                # "due today", not "missed".
                weeks_left = max(days_left / 7, 1)
                weekly_needed = remaining / weeks_left
                insights.append({
                    "level": "info",
                    "key": "weekly_savings_needed",
                    "params": {"name": g["name"], "amount": round(weekly_needed, 2), "deadline": g["deadline"]},
                })
            else:
                insights.append({
                    "level": "warning",
                    "key": "goal_deadline_passed",
                    "params": {"name": g["name"], "remaining": round(remaining, 2)},
                })

    if not insights:
        insights.append({"level": "success", "key": "on_track", "params": {}})

    return insights


def compute_goal_progress(goal):
    """Enrich a Goal row with computed progress fields (pct funded, amount remaining, days left)."""
    target = goal["target_amount"]
    current = goal["current_amount"]
    remaining = max(target - current, 0)
    pct = round(min(current / target * 100, 100), 1) if target > 0 else 0.0

    days_left = None
    if goal["deadline"]:
        try:
            days_left = (date.fromisoformat(goal["deadline"]) - date.today()).days
        except ValueError:
            days_left = None

    return {**goal, "remaining": round(remaining, 2), "pct": pct, "days_left": days_left}


# ---------------------------------------------------------------------------
# Natural-language chat parsing (rule-based, bilingual EN/ZH keyword matching)
# ---------------------------------------------------------------------------
BUDGET_KEYWORDS = ["budget", "预算", "余额"]
# "还剩"/"remaining" only implies a budget question when no goal word is present.
BUDGET_AMBIGUOUS_KEYWORDS = ["how much do i have", "remaining", "left in", "还剩", "剩多少"]
GOAL_KEYWORDS = ["goal", "savings goal", "progress", "目标", "进度"]
EXPENSE_KEYWORDS = [
    "spent", "spend", "paid", "pay ", "paying", "bought", "buy", "cost",
    "花了", "花", "买了", "买", "支付", "付了", "付款",
]
# Used only to find the amount: a purchase verb ("bought 2 coffees") is not followed by the price.
AMOUNT_EXPENSE_KEYWORDS = [
    "spent", "spend", "paid", "pay", "cost",
    "花了", "花", "支付", "付了", "付款",
]
INCOME_KEYWORDS = [
    "earned", "earn", "received", "receive", "income", "got paid",
    "收入", "赚了", "赚", "领到", "到账", "入账",
]
# Nouns that can state income without a verb ("my allowance is 800", "工资3000").
INCOME_NOUN_KEYWORDS = [
    "allowance", "salary", "paycheck", "scholarship", "part-time", "parttime", "part time",
    "工资", "薪水", "奖学金", "生活费", "兼职", "补贴",
]

CATEGORY_KEYWORDS = {
    "rent": ["rent", "房租"],
    "food": ["food", "meal", "lunch", "dinner", "breakfast", "restaurant", "coffee", "吃饭", "餐", "饭", "咖啡"],
    "groceries": ["groceries", "grocery", "supermarket", "日用", "超市", "采购"],
    "utilities": ["utilities", "utility", "electric bill", "water bill", "水电"],
    "transport": ["transport", "bus", "taxi", "uber", "subway", "gas", "交通", "打车", "公交", "地铁", "打的"],
    "tuition": ["tuition", "school fee", "学费"],
    "healthcare": ["healthcare", "doctor", "medicine", "hospital", "医疗", "看病", "药"],
    "phone": ["phone bill", "phone", "话费", "电话费"],
    "entertainment": ["entertainment", "movie", "game", "concert", "娱乐", "电影", "游戏"],
    "shopping": ["shopping", "clothes", "购物", "衣服"],
    "subscriptions": ["subscription", "netflix", "spotify", "订阅"],
    "travel": ["travel", "trip", "flight", "旅行", "旅游", "机票"],
    "emergency": ["emergency", "紧急"],
    "books": ["book", "textbook", "书", "教材"],
    "courses": ["course", "class", "课程", "培训"],
    "software": ["software", "app subscription", "软件"],
    "career": ["career", "internship", "职业", "实习"],
    "workshop": ["workshop", "工作坊"],
    "allowance": ["allowance", "生活费"],
    "scholarship": ["scholarship", "奖学金"],
    "job": ["salary", "job", "工资", "薪水"],
    "parttime": ["part-time", "parttime", "part time", "兼职"],
}

_QUOTE_PATTERNS = [r'"([^"]+)"', r'“([^”]+)”', r'「([^」]+)」', r"'([^']+)'"]


def _extract_quoted_name(text):
    for pattern in _QUOTE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _match_category(lower_text, txn_type):
    allowed = INCOME_CATEGORIES if txn_type == "income" else (SURVIVAL_CATEGORIES | LEARNING_CATEGORIES | BUFFER_CATEGORIES)
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category not in allowed:
            continue
        if any(kw in lower_text for kw in keywords):
            return category
    return "income" if txn_type == "income" else "other"


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY_AFTER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:块钱|元|块|rmb|cny|yuan)", re.IGNORECASE
)
_CURRENCY_BEFORE_RE = re.compile(r"[¥￥]\s*(\d+(?:\.\d+)?)")
_PRICE_BEFORE_RE = re.compile(
    r"(?:for|on|at|共|一共|总共)\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)


_CN_QUANTITY_RE = re.compile(
    r"\s*(?:杯|个|次|件|本|张|份|瓶|包|盒|公斤|千克|斤|克|人|岁|天|年|月|日|小时|分钟|公里|米)"
)
_EN_QUANTITY_RE = re.compile(
    r"\s*(?:cups?|pieces?|items?|times?|hours?|days?|months?|years?|"
    r"people|persons?|km|kg|ml|g|l)\b",
    re.IGNORECASE,
)


def _is_quantity(text, number_end):
    tail = text[number_end : number_end + 8]
    return bool(_CN_QUANTITY_RE.match(tail) or _EN_QUANTITY_RE.match(tail))


def _keyword_positions(lower, keywords):
    positions = set()
    for keyword in keywords:
        start = lower.find(keyword)
        while start != -1:
            positions.add(start)
            start = lower.find(keyword, start + 1)
    return positions


def _nearest_number(numbers, positions):
    def distance(number):
        start, end, _, _ = number
        return min(min(abs(start - pos), abs(end - pos)) for pos in positions)

    return min(numbers, key=distance)


def _extract_amount(text, txn_type):
    """Pick the number closest to a spending/income keyword, ignoring quantities.

    This handles amounts before the keyword ("30 spent", "800到账") and avoids
    choosing a quantity ("2杯咖啡", "2 hours"). Without a keyword, a currency marker
    or a price preposition must identify the price; a bare count is never a price.
    """
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return None
    numbers = [
        (
            match.start(),
            match.end(),
            float(match.group(0)),
            _is_quantity(text, match.end()),
        )
        for match in matches
    ]
    currency_starts = {match.start(1) for match in _CURRENCY_AFTER_RE.finditer(text)}
    currency_starts.update(
        match.start(1) for match in _CURRENCY_BEFORE_RE.finditer(text)
    )
    price_starts = {match.start(1) for match in _PRICE_BEFORE_RE.finditer(text)}

    groups = (
        [AMOUNT_EXPENSE_KEYWORDS]
        if txn_type == "expense"
        else [INCOME_KEYWORDS, INCOME_NOUN_KEYWORDS]
    )
    lower = text.lower()
    for keywords in groups:
        positions = _keyword_positions(lower, keywords)
        if not positions:
            continue
        candidates = [number for number in numbers if not number[3]]
        if candidates:
            return _nearest_number(candidates, positions)[2]

    marked = [number for number in numbers if number[0] in currency_starts]
    if marked:
        return marked[0][2]
    priced = [number for number in numbers if number[0] in price_starts]
    if priced:
        return priced[0][2]
    return None


def _matches(text, keywords):
    return any(keyword in text for keyword in keywords)


def parse_chat_message(message):
    """Turn freeform chat text into an intent + extracted entities (no DB access).

    A number alone is not a transaction: "我住在3号楼" and "I have 100 yuan" must
    not be booked as an expense. A spending/income cue selects `add_transaction`;
    the amount is then taken from that clause (see `_extract_amount`).
    """
    text = (message or "").strip()
    if not text:
        return {"intent": "help"}
    lower = text.lower()

    has_expense = _matches(lower, EXPENSE_KEYWORDS)
    if "got paid" in lower:
        # "got paid" contains the expense keyword "paid"; the phrase itself is income.
        has_expense = _matches(
            lower, [keyword for keyword in EXPENSE_KEYWORDS if keyword != "paid"]
        )
    has_income_verb = _matches(lower, INCOME_KEYWORDS)
    has_income_noun = _matches(lower, INCOME_NOUN_KEYWORDS)
    has_income = has_income_verb or has_income_noun
    txn_type = "income" if (has_income and not has_expense) else "expense"

    if has_expense or has_income:
        amount = _extract_amount(text, txn_type)
        if amount is not None:
            return {
                "intent": "add_transaction",
                "amount": amount,
                "txn_type": txn_type,
                "category": _match_category(lower, txn_type),
            }

    # A clear transaction clause (with an amount) wins over "还剩"/"budget"; without one,
    # goal words outrank the ambiguous remaining-budget words ("目标还剩多少").
    if _matches(lower, GOAL_KEYWORDS):
        return {"intent": "goal_progress", "goal_name": _extract_quoted_name(text)}
    if _matches(lower, BUDGET_KEYWORDS) or _matches(lower, BUDGET_AMBIGUOUS_KEYWORDS):
        return {"intent": "budget_status"}
    if has_expense or has_income_verb:
        # "I bought a coffee": a transaction cue without a number asks for the amount.
        return {
            "intent": "add_transaction",
            "amount": None,
            "txn_type": txn_type,
            "category": None,
        }
    return {"intent": "help"}
