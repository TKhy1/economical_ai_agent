"""Input-validation contract for every mutating endpoint.

These tests pin the JSON error shape the frontend relies on: a 4xx status plus
``{"error": "..."}``, never a Flask HTML error page, never a 500.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support

import limits


class ProfileValidationTest(support.ApiTestCase):
    def test_missing_body_is_rejected(self):
        resp = self.client.put("/api/profile")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_non_object_body_is_rejected(self):
        for payload in ([1, 2, 3], "just text", 42):
            with self.subTest(payload=payload):
                resp = self.client.put("/api/profile", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_non_string_names_are_rejected(self):
        for name in (42, 3.5, True, [1], {"a": 1}):
            with self.subTest(name=name):
                resp = self.client.put("/api/profile", json={"name": name})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "name must be a string")

    def test_unencodable_names_are_rejected(self):
        resp = self.client.put("/api/profile", json={"name": "\ud800"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "name must be valid UTF-8 text")

    def test_blank_name_is_rejected(self):
        resp = self.client.put("/api/profile", json={"name": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "name must not be empty")

    def test_valid_name_is_trimmed_and_stored(self):
        resp = self.client.put("/api/profile", json={"name": "  Ada Lovelace  "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "Ada Lovelace")
        self.assertEqual(self.client.get("/api/profile").get_json()["name"], "Ada Lovelace")

    def test_partial_update_keeps_the_stored_name(self):
        self.client.put("/api/profile", json={"name": "Ada"})
        resp = self.client.put("/api/profile", json={"risk_level": "aggressive"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["name"], "Ada")
        self.assertEqual(body["risk_level"], "aggressive")

    def test_invalid_risk_level_is_rejected(self):
        resp = self.client.put("/api/profile", json={"risk_level": "yolo"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"],
            "risk_level must be conservative, moderate, or aggressive",
        )

    def test_invalid_language_is_rejected(self):
        resp = self.client.put("/api/profile", json={"language": "fr"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "language must be en or zh")

    def test_non_numeric_numbers_are_rejected(self):
        resp = self.client.put("/api/profile", json={"monthly_income": "lots"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"],
            "security_threshold and monthly_income must be numbers",
        )

    def test_valid_numeric_strings_are_coerced(self):
        resp = self.client.put(
            "/api/profile", json={"monthly_income": "3000", "security_threshold": "150.5"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["monthly_income"], 3000.0)
        self.assertEqual(resp.get_json()["security_threshold"], 150.5)

    def test_boolean_numbers_are_rejected(self):
        resp = self.client.put("/api/profile", json={"monthly_income": True})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"],
            "security_threshold and monthly_income must be numbers",
        )

    def json_error(self, resp):
        body = resp.get_json()
        self.assertIsInstance(body, dict)
        return body.get("error")


class TransactionValidationTest(support.ApiTestCase):
    def test_missing_fields_are_rejected(self):
        cases = [
            {},
            {"amount": 10},
            {"amount": 10, "type": "expense"},
            {"amount": 10, "category": "food"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = self.client.post("/api/transactions", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"],
                    "amount, type, and category are required",
                )

    def test_non_object_body_is_rejected(self):
        resp = self.client.post("/api/transactions", json=[1, 2, 3])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_amount_must_be_positive(self):
        for amount in (0, -5, "abc", None):
            with self.subTest(amount=amount):
                resp = self.client.post(
                    "/api/transactions",
                    json={"amount": amount, "type": "expense", "category": "food"},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "amount must be positive")

    def test_non_finite_amounts_are_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                resp = self.client.post(
                    "/api/transactions",
                    data='{"amount": %s, "type": "expense", "category": "food"}' % token,
                    content_type="application/json",
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "amount must be positive")

    def test_boolean_amounts_are_rejected(self):
        # Python's float(True) == 1.0 must never silently book "amount": true.
        for amount in (True, False):
            with self.subTest(amount=amount):
                resp = self.client.post(
                    "/api/transactions",
                    json={"amount": amount, "type": "expense", "category": "food"},
                )
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "amount must be positive")
        self.assertEqual(self.count_transactions(), 0)

    def test_ledger_add_rejects_boolean_amounts(self):
        resp = self.client.post(
            "/api/ledger/add",
            json={"amount": True, "type": "expense", "category": "food"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "amount must be positive")
        self.assertEqual(self.count_transactions(), 0)

    def test_invalid_type_is_rejected(self):
        resp = self.client.post(
            "/api/transactions", json={"amount": 1, "type": "transfer", "category": "food"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "type must be income or expense")

    def test_blank_category_is_rejected(self):
        resp = self.client.post(
            "/api/transactions", json={"amount": 1, "type": "expense", "category": "   "}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "category is required")

    def test_invalid_occurred_at_is_rejected(self):
        resp = self.client.post(
            "/api/transactions",
            json={
                "amount": 1,
                "type": "expense",
                "category": "food",
                "occurred_at": "last tuesday",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"], "occurred_at must be a valid ISO timestamp"
        )

    def test_valid_transaction_returns_the_created_row(self):
        resp = self.client.post(
            "/api/transactions",
            json={"amount": 12.5, "type": "expense", "category": "Food", "note": "lunch"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIsInstance(body["id"], int)
        self.assertEqual(body["amount"], 12.5)
        self.assertEqual(body["category"], "food")
        self.assertEqual(body["tier"], "survival")
        self.assertEqual(body["note"], "lunch")

    def test_tier_assignment_covers_all_pools(self):
        cases = [
            ("food", "expense", "survival"),
            ("books", "expense", "learning"),
            ("entertainment", "expense", "buffer"),
            ("job", "income", "income"),
        ]
        for category, txn_type, tier in cases:
            with self.subTest(category=category):
                resp = self.client.post(
                    "/api/transactions",
                    json={"amount": 5, "type": txn_type, "category": category},
                )
                self.assertEqual(resp.status_code, 201)
                self.assertEqual(resp.get_json()["tier"], tier)

    def test_ledger_add_returns_budget_status(self):
        resp = self.client.post(
            "/api/ledger/add",
            json={"amount": 40, "type": "expense", "category": "groceries"},
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIn("transaction", body)
        self.assertIn("budget_status", body)
        status = body["budget_status"]
        for key in (
            "tiers",
            "income_total",
            "expense_total",
            "net_balance",
            "locked_amount",
            "available_balance",
        ):
            self.assertIn(key, status)
        self.assertEqual(status["expense_total"], 40.0)
        self.assertEqual(status["net_balance"], -40.0)

    def test_transactions_can_be_listed_and_deleted(self):
        created = self.client.post(
            "/api/transactions",
            json={"amount": 9, "type": "expense", "category": "food"},
        ).get_json()
        listed = self.client.get("/api/transactions?limit=5").get_json()
        self.assertEqual([t["id"] for t in listed], [created["id"]])
        resp = self.client.delete(f"/api/transactions/{created['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"deleted": created["id"]})
        self.assertEqual(self.client.get("/api/transactions").get_json(), [])

    def test_list_limit_is_validated_and_clamped(self):
        resp = self.client.get("/api/transactions?limit=abc")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "limit must be an integer")

        self.client.post(
            "/api/transactions",
            json={"amount": 1, "type": "expense", "category": "food"},
        )
        clamped = self.client.get("/api/transactions?limit=999999999999999999999")
        self.assertEqual(clamped.status_code, 200)
        self.assertEqual(len(clamped.get_json()), 1)

    def test_out_of_range_ids_do_not_500(self):
        resp = self.client.delete("/api/transactions/9223372036854775808")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "transaction not found")

    def test_deleting_a_missing_transaction_is_404(self):
        resp = self.client.delete("/api/transactions/999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "transaction not found")


class GoalValidationTest(support.ApiTestCase):
    def test_create_requires_name_and_target(self):
        for payload in ({}, {"name": "Trip"}, {"target_amount": 100}, {"name": "  ", "target_amount": 1}):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/goals", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"], "name and target_amount are required"
                )

    def test_non_numeric_target_is_rejected(self):
        resp = self.client.post("/api/goals", json={"name": "Trip", "target_amount": "lots"})
        self.assertEqual(resp.status_code, 400)

    def test_non_numeric_current_amount_is_rejected(self):
        resp = self.client.post(
            "/api/goals",
            json={"name": "Trip", "target_amount": 100, "current_amount": "x"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "current_amount must be a number")

    def test_create_then_update_goal(self):
        created = self.client.post(
            "/api/goals", json={"name": "Trip", "target_amount": 500, "current_amount": 50}
        )
        self.assertEqual(created.status_code, 201)
        goal_id = created.get_json()["id"]

        updated = self.client.put(
            f"/api/goals/{goal_id}", json={"current_amount": 200, "name": "Trip 2026"}
        )
        self.assertEqual(updated.status_code, 200)
        body = updated.get_json()
        self.assertEqual(body["current_amount"], 200.0)
        self.assertEqual(body["name"], "Trip 2026")

    def test_update_missing_goal_is_404(self):
        resp = self.client.put("/api/goals/12345", json={"name": "Nope"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "goal not found")

    def test_out_of_range_goal_ids_are_404(self):
        for resp in (
            self.client.put("/api/goals/9223372036854775808", json={"name": "x"}),
            self.client.delete("/api/goals/9223372036854775808"),
            self.client.get("/api/goal/progress?id=9223372036854775808"),
        ):
            self.assertEqual(resp.status_code, 404)
            self.assertEqual(resp.get_json()["error"], "goal not found")

    def test_goal_progress_unknown_id_is_404(self):
        resp = self.client.get("/api/goal/progress?id=999")
        self.assertEqual(resp.status_code, 404)

    def test_deleting_a_missing_goal_is_404(self):
        resp = self.client.delete("/api/goals/999999")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "goal not found")

    def test_goal_progress_rejects_non_integer_ids(self):
        # Previously an unparsable id fell through to the full goal list.
        for raw in ("abc", "1.5", "5x", ""):
            with self.subTest(raw=raw):
                resp = self.client.get(f"/api/goal/progress?id={raw}")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "id must be an integer")


class BudgetValidationTest(support.ApiTestCase):
    def test_invalid_periods_are_rejected(self):
        for path in ("/api/budget", "/api/budget/status", "/api/insights"):
            with self.subTest(path=path):
                resp = self.client.get(f"{path}?period=daily")
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(
                    resp.get_json()["error"], "period must be weekly or monthly"
                )

    def test_budget_update_validates_period(self):
        resp = self.client.put("/api/budget", json={"period_type": "daily"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.get_json()["error"], "period_type must be weekly or monthly"
        )

    def test_budget_update_validates_limits(self):
        resp = self.client.put("/api/budget", json={"survival_limit": "lots"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "limits must be numbers")

    def test_budget_update_persists_limits(self):
        resp = self.client.put(
            "/api/budget",
            json={
                "period_type": "weekly",
                "survival_limit": 100,
                "buffer_limit": 40,
                "learning_limit": 60,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["survival_limit"], 100.0)
        self.assertEqual(body["buffer_limit"], 40.0)
        self.assertEqual(body["learning_limit"], 60.0)

        fetched = self.client.get("/api/budget?period=weekly").get_json()
        self.assertEqual(fetched["survival_limit"], 100.0)


class ChatValidationTest(support.ApiTestCase):
    def test_message_is_required(self):
        for payload in ({}, {"message": ""}, {"message": "   "}):
            with self.subTest(payload=payload):
                resp = self.client.post("/api/chat", json=payload)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "message is required")

    def test_non_object_body_is_rejected(self):
        resp = self.client.post("/api/chat", json="hello")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "a JSON object is required")

    def test_decision_must_be_confirm_or_cancel(self):
        resp = self.client.post("/api/chat/action", json={"id": "x", "decision": "maybe"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "decision must be confirm or cancel")

    def test_action_id_is_required(self):
        resp = self.client.post("/api/chat/action", json={"decision": "confirm"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "action id is required")

    def test_unknown_action_is_404(self):
        resp = self.client.post(
            "/api/chat/action", json={"id": "not-a-real-id", "decision": "confirm"}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "action not found")

    def test_expense_needs_confirmation_before_it_is_written(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        self.assertIn("action", reply)
        self.assertIsNotNone(reply["action"])
        self.assertEqual(reply["key"], "chat_action_preview")
        self.assertEqual(self.count_transactions(), 0)

        confirmed = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "confirm"}
        )
        self.assertEqual(confirmed.status_code, 201)
        self.assertEqual(confirmed.get_json()["status"], "confirmed")
        self.assertEqual(self.count_transactions(), 1)

    def test_cancel_does_not_write(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        cancelled = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "cancel"}
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["status"], "cancelled")
        self.assertEqual(self.count_transactions(), 0)

        again = self.client.post(
            "/api/chat/action", json={"id": reply["action"]["id"], "decision": "confirm"}
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["error"], "action already cancelled")

    def test_double_confirm_is_rejected(self):
        reply = self.client.post("/api/chat", json={"message": "spent 30 on food"}).get_json()
        action_id = reply["action"]["id"]
        self.client.post("/api/chat/action", json={"id": action_id, "decision": "confirm"})
        again = self.client.post(
            "/api/chat/action", json={"id": action_id, "decision": "confirm"}
        )
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.get_json()["error"], "action already consumed")

    def test_expired_action_is_rejected(self):
        action_id = self.seed_expired_action()
        resp = self.client.post(
            "/api/chat/action", json={"id": action_id, "decision": "confirm"}
        )
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.get_json()["error"], "action expired")
        self.assertEqual(self.count_transactions(), 0)

    def test_budget_question_answers_without_an_action(self):
        reply = self.client.post("/api/chat", json={"message": "what is my budget"}).get_json()
        self.assertIsNone(reply["action"])
        self.assertEqual(reply["key"], "chat_budget_status")
        for key in (
            "net_balance",
            "available_balance",
            "survival_remaining",
            "buffer_remaining",
            "learning_remaining",
        ):
            self.assertIn(key, reply["params"])

    def test_message_without_amount_asks_for_one(self):
        reply = self.client.post("/api/chat", json={"message": "I bought a coffee"}).get_json()
        self.assertEqual(reply["key"], "chat_missing_amount")


class P0InputHardeningTest(support.ApiTestCase):
    """Client input that used to reach float()/sqlite3 and surface as a 500."""

    HUGE = 10 ** 400

    def test_non_string_chat_message_is_rejected(self):
        for message in (123, 12.5, True, {"a": 1}, [1, 2]):
            with self.subTest(message=message):
                resp = self.client.post("/api/chat", json={"message": message})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.get_json()["error"], "message must be a string")
        self.assertEqual(self.count_transactions(), 0)

    def test_non_string_notes_are_rejected(self):
        for note in ({}, [1], 123, True, "\ud800"):
            for path in ("/api/ledger/add", "/api/transactions"):
                with self.subTest(note=note, path=path):
                    resp = self.client.post(
                        path,
                        json={
                            "amount": 10,
                            "type": "expense",
                            "category": "food",
                            "note": note,
                        },
                    )
                    self.assertEqual(resp.status_code, 400)
                    self.assertEqual(resp.get_json()["error"], "note must be a string")
        self.assertEqual(self.count_transactions(), 0)

    def test_non_string_categories_are_rejected(self):
        for category in (123, {}, [], True, "\ud800"):
            for path in ("/api/ledger/add", "/api/transactions"):
                with self.subTest(category=category, path=path):
                    resp = self.client.post(
                        path,
                        json={"amount": 10, "type": "expense", "category": category},
                    )
                    self.assertEqual(resp.status_code, 400)
                    self.assertEqual(
                        resp.get_json()["error"], "category must be a string"
                    )
        self.assertEqual(self.count_transactions(), 0)

    def test_null_or_blank_note_is_allowed(self):
        for note in (None, "", "   "):
            with self.subTest(note=note):
                resp = self.client.post(
                    "/api/ledger/add",
                    json={
                        "amount": 10,
                        "type": "expense",
                        "category": "food",
                        "note": note,
                    },
                )
                self.assertEqual(resp.status_code, 201)

    def test_oversized_numbers_are_rejected_without_writing(self):
        huge = self.HUGE
        cases = (
            ("put", "/api/profile", {"monthly_income": huge}),
            ("put", "/api/profile", {"security_threshold": huge}),
            ("put", "/api/budget", {"survival_limit": huge}),
            ("post", "/api/goals", {"name": "g", "target_amount": huge}),
            (
                "post",
                "/api/goals",
                {"name": "g", "target_amount": 10, "current_amount": huge},
            ),
            (
                "post",
                "/api/ledger/add",
                {"amount": huge, "type": "expense", "category": "food"},
            ),
            (
                "post",
                "/api/transactions",
                {"amount": -huge, "type": "expense", "category": "food"},
            ),
        )
        for method, path, payload in cases:
            with self.subTest(path=path, payload=payload):
                resp = getattr(self.client, method)(path, json=payload)
                self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))

        self.assertEqual(self.count_transactions(), 0)
        self.assertEqual(self.client.get("/api/goals").get_json(), [])

    def test_rejected_budget_update_does_not_create_a_plan(self):
        conn = self.raw_connection()
        before = conn.execute("SELECT COUNT(*) FROM BudgetPlan").fetchone()[0]
        conn.close()

        for payload in (
            {"survival_limit": self.HUGE},
            {"survival_limit": -1},
            {"survival_limit": "lots"},
        ):
            with self.subTest(payload=payload):
                resp = self.client.put("/api/budget", json=payload)
                self.assertEqual(resp.status_code, 400)

        conn = self.raw_connection()
        after = conn.execute("SELECT COUNT(*) FROM BudgetPlan").fetchone()[0]
        conn.close()
        self.assertEqual(before, after)

    def test_negative_numbers_are_rejected(self):
        cases = (
            ("put", "/api/profile", {"security_threshold": -1}),
            ("put", "/api/profile", {"monthly_income": -1}),
            ("put", "/api/budget", {"survival_limit": -1}),
            ("post", "/api/goals", {"name": "g", "target_amount": -1}),
            (
                "post",
                "/api/goals",
                {"name": "g", "target_amount": 10, "current_amount": -1},
            ),
            ("post", "/api/goals", {"name": "g", "target_amount": 0}),
        )
        for method, path, payload in cases:
            with self.subTest(path=path, payload=payload):
                resp = getattr(self.client, method)(path, json=payload)
                self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))

        self.assertEqual(self.client.get("/api/goals").get_json(), [])

    def test_deeply_nested_json_is_rejected(self):
        nested = "[" * 20000 + "]" * 20000
        for method, path in (
            ("put", "/api/profile"),
            ("post", "/api/chat"),
            ("post", "/api/chat/action"),
            ("post", "/api/ledger/add"),
            ("post", "/api/transactions"),
            ("post", "/api/goals"),
            ("put", "/api/budget"),
        ):
            with self.subTest(path=path):
                resp = getattr(self.client, method)(
                    path, data=nested, content_type="application/json"
                )
                self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))

    def test_json_body_without_content_type_is_accepted(self):
        # The routes force-parse the body so a client that forgets the header still works.
        resp = self.client.put("/api/profile", data='{"name": "Ada"}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], "Ada")

        resp = self.client.post(
            "/api/ledger/add",
            data='{"amount": 5, "type": "expense", "category": "food"}',
        )
        self.assertEqual(resp.status_code, 201)

    def test_huge_number_in_chat_text_is_not_a_transaction(self):
        # float("9" * 400) is inf; it must ask for an amount rather than build an action card.
        reply = self.client.post(
            "/api/chat", json={"message": "I spent " + "9" * 400 + " yuan on food"}
        ).get_json()
        self.assertEqual(reply["key"], "chat_missing_amount")
        self.assertIsNone(reply.get("action"))
        self.assertEqual(self.count_transactions(), 0)

    def test_goal_name_must_be_storable_text(self):
        resp = self.client.post(
            "/api/goals", json={"name": "\ud800", "target_amount": 10}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "name must be valid UTF-8 text")

        created = self.client.post(
            "/api/goals", json={"name": "Trip", "target_amount": 10}
        ).get_json()
        resp = self.client.put(f"/api/goals/{created['id']}", json={"name": "\ud800"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/goal/progress?id={created['id']}").get_json()["name"],
            "Trip",
        )


class ExtremeMoneyTest(support.ApiTestCase):
    """Capped money keeps every aggregate finite, so JSON never contains Infinity."""

    def test_money_above_the_cap_is_rejected(self):
        too_much = limits.MAX_AMOUNT * 10
        cases = (
            (
                "post",
                "/api/ledger/add",
                {"amount": too_much, "type": "expense", "category": "food"},
            ),
            (
                "put",
                "/api/profile",
                {"security_threshold": too_much, "monthly_income": too_much},
            ),
            ("put", "/api/budget", {"survival_limit": too_much}),
            ("post", "/api/goals", {"name": "g", "target_amount": too_much}),
            (
                "post",
                "/api/goals",
                {"name": "g", "target_amount": 10, "current_amount": too_much},
            ),
        )
        for method, path, payload in cases:
            with self.subTest(path=path):
                resp = getattr(self.client, method)(path, json=payload)
                self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))
        self.assertEqual(self.count_transactions(), 0)

    def _assert_finite_json(self, path):
        raw = self.client.get(path).get_data(as_text=True)
        for token in ("Infinity", "-Infinity", "NaN"):
            self.assertNotIn(token, raw)
        json.loads(
            raw,
            parse_constant=lambda value: self.fail(f"non-finite JSON token: {value}"),
        )

    def test_capped_expenses_keep_budget_and_insights_finite(self):
        for _ in range(2):
            resp = self.client.post(
                "/api/ledger/add",
                json={
                    "amount": limits.MAX_AMOUNT,
                    "type": "expense",
                    "category": "food",
                },
            )
            self.assertEqual(resp.status_code, 201)

        self._assert_finite_json("/api/budget/status?period=weekly")
        self._assert_finite_json("/api/insights")

    def test_capped_goals_keep_locked_amount_finite(self):
        for index in range(2):
            resp = self.client.post(
                "/api/goals",
                json={"name": f"g{index}", "target_amount": limits.MAX_AMOUNT},
            )
            self.assertEqual(resp.status_code, 201)

        self._assert_finite_json("/api/budget/status?period=weekly")


class InputLengthLimitTest(support.ApiTestCase):
    def test_long_names_are_rejected(self):
        long_name = "n" * (limits.MAX_NAME_LENGTH + 1)
        resp = self.client.put("/api/profile", json={"name": long_name})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "name is too long")

        resp = self.client.post(
            "/api/goals", json={"name": long_name, "target_amount": 10}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "name is too long")
        self.assertEqual(self.client.get("/api/goals").get_json(), [])

    def test_long_transaction_text_is_rejected(self):
        resp = self.client.post(
            "/api/ledger/add",
            json={
                "amount": 10,
                "type": "expense",
                "category": "c" * (limits.MAX_CATEGORY_LENGTH + 1),
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "category is too long")

        resp = self.client.post(
            "/api/ledger/add",
            json={
                "amount": 10,
                "type": "expense",
                "category": "food",
                "note": "n" * (limits.MAX_NOTE_LENGTH + 1),
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "note is too long")
        self.assertEqual(self.count_transactions(), 0)

    def test_long_chat_message_is_rejected(self):
        resp = self.client.post(
            "/api/chat",
            json={"message": "m" * (limits.MAX_MESSAGE_LENGTH + 1)},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "message is too long")

    def test_boundary_lengths_are_accepted(self):
        name = "n" * limits.MAX_NAME_LENGTH
        resp = self.client.put("/api/profile", json={"name": name})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["name"], name)

        resp = self.client.post("/api/goals", json={"name": name, "target_amount": 10})
        self.assertEqual(resp.status_code, 201)

        resp = self.client.post(
            "/api/ledger/add",
            json={
                "amount": 10,
                "type": "expense",
                "category": "c" * limits.MAX_CATEGORY_LENGTH,
                "note": "n" * limits.MAX_NOTE_LENGTH,
            },
        )
        self.assertEqual(resp.status_code, 201)

        resp = self.client.post(
            "/api/chat", json={"message": "m" * limits.MAX_MESSAGE_LENGTH}
        )
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()

