"""Shared input limits.

Money is capped so aggregates (income/expense totals, locked goal amounts) can never
overflow to ``inf`` and produce invalid JSON (``Infinity``). Text caps keep a single
request from storing or echoing megabytes of data in this demo.
"""

# Largest accepted monetary value, in CNY. Two capped transactions sum to a finite
# double, so the budget/insights response always contains valid JSON.
MAX_AMOUNT = 10 ** 12

MAX_NAME_LENGTH = 120
MAX_CATEGORY_LENGTH = 60
MAX_NOTE_LENGTH = 1000
MAX_MESSAGE_LENGTH = 4000
