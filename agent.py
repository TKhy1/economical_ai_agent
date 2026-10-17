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
    category = (category or "").strip().lower()
    if txn_type == "income" or category in INCOME_CATEGORIES:
        return "income"
    if category in SURVIVAL_CATEGORIES:
        return "survival"
    if category in LEARNING_CATEGORIES:
        return "learning"
    return "buffer"


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

    if budget_status["net_balance"] < profile["security_threshold"]:
        insights.append({
            "level": "danger",
            "key": "balance_below_threshold",
            "params": {
                "balance": round(budget_status["net_balance"], 2),
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
            if days_left > 0:
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
BUDGET_KEYWORDS = ["budget", "how much do i have", "remaining", "left in", "预算", "还剩", "剩多少", "余额"]
GOAL_KEYWORDS = ["goal", "savings goal", "progress", "目标", "进度"]
EXPENSE_KEYWORDS = [
    "spent", "spend", "paid", "pay", "bought", "buy", "cost",
    "花了", "花", "买了", "买", "支付", "付了", "付款",
]
INCOME_KEYWORDS = [
    "earned", "earn", "received", "receive", "income", "got paid",
    "收入", "赚了", "赚", "领到", "到账", "入账",
]

CATEGORY_KEYWORDS = {
    "rent": ["rent", "房租"],
    "food": ["food", "meal", "lunch", "dinner", "breakfast", "restaurant", "吃饭", "餐", "饭"],
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
    "job": ["salary", "job", "工资"],
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


def parse_chat_message(message):
    """Turn freeform chat text into an intent + extracted entities (no DB access)."""
    text = (message or "").strip()
    lower = text.lower()

    amount = None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if match:
        amount = float(match.group(1))

    if any(kw in lower for kw in BUDGET_KEYWORDS):
        return {"intent": "budget_status"}

    if any(kw in lower for kw in GOAL_KEYWORDS):
        return {"intent": "goal_progress", "goal_name": _extract_quoted_name(text)}

    has_expense = any(kw in lower for kw in EXPENSE_KEYWORDS)
    has_income = any(kw in lower for kw in INCOME_KEYWORDS)

    if amount is not None or has_expense or has_income:
        txn_type = "income" if (has_income and not has_expense) else "expense"
        category = _match_category(lower, txn_type) if amount is not None else None
        return {"intent": "add_transaction", "amount": amount, "txn_type": txn_type, "category": category}

    return {"intent": "help"}
