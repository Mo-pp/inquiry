import unittest
from datetime import datetime, timezone

from filter_agent.services.lead_outreach import (
    LeadOutreachExecutor,
    LeadOutreachPlanner,
    LeadOutreachService,
    LeadOutreachWorker,
    OutreachStatus,
    RetryPolicy,
    render_first_message,
)


def fake_lead(**overrides):
    value = {
        "source_lead_id": "dry-run-lead-1",
        "whatsapp_number": "+15555550100",
        "platform": "Facebook",
        "business_role_answer": "buyer",
        "target_country_answer": "Nigeria",
        "coverage_area_answer": "over 500 sqm",
        "purchase_purpose_answer": "resell",
        "frequency_budget_quantity_answer": "4G, 10 units",
    }
    value.update(overrides)
    return value


class LeadOutreachTests(unittest.TestCase):
    def test_planner_is_read_only_in_dry_run_and_persists_once_when_enabled(self):
        class Source:
            def find_unplanned_outreach_leads(self, *, limit):
                self.limit = limit
                return [fake_lead()]

        class Store:
            def __init__(self):
                self.plans = []

            def ensure_plan(self, plan):
                self.plans.append(plan)

        source = Source()
        store = Store()
        service = LeadOutreachService(dry_run=True)
        planner = LeadOutreachPlanner(source, store, service)

        dry_run = planner.run_once(limit=1, dry_run=True)
        self.assertEqual(dry_run.scanned, 1)
        self.assertEqual(dry_run.persisted, 0)
        self.assertEqual(store.plans, [])
        self.assertEqual(dry_run.status_counts, {OutreachStatus.PENDING_APPROVAL.value: 1})

        persisted = planner.run_once(limit=1, dry_run=False)
        self.assertEqual(persisted.persisted, 1)
        self.assertEqual(len(store.plans), 1)

    def test_worker_rebuilds_approved_plan_without_calling_executor_in_dry_run(self):
        class Job:
            source_lead_id = "dry-run-lead-1"

        class Source:
            def list_calls(self):
                return getattr(self, "calls", 0)

            def get_by_source_lead_id(self, source_lead_id):
                self.calls = self.list_calls() + 1
                self.source_lead_id = source_lead_id
                return fake_lead(source_lead_id=source_lead_id)

        class Store:
            def list_ready_jobs(self, *, limit):
                self.limit = limit
                return [Job()]

        class Executor:
            def __init__(self):
                self.calls = []

            def run(self, plan, *, execute):
                self.calls.append((plan, execute))
                return {"status": "dry_run", "would_send": plan.would_send}

        source = Source()
        executor = Executor()
        service = LeadOutreachService(
            dry_run=True,
            allowlist=["+15555550100"],
            known_phone_checker=lambda _phone: False,
        )
        run = LeadOutreachWorker(source, Store(), service, executor).run_once(
            limit=1, dry_run=True
        )
        self.assertEqual(run.checked, 1)
        self.assertEqual(run.results, ({"status": "dry_run", "would_send": True},))
        self.assertEqual(executor.calls[0][1], False)

    def test_template_uses_form_answers_and_caps_questions(self):
        text = render_first_message(fake_lead(), custom_questions=["What is the site address?"])
        self.assertIn("Nigeria", text)
        self.assertIn("4G, 10 units", text)
        self.assertIn("What is the site address?", text)
        with self.assertRaises(ValueError):
            render_first_message(fake_lead(), custom_questions=["1", "2", "3", "4"])

    def test_template_selects_three_positive_questions_when_no_override(self):
        text = render_first_message(fake_lead())
        self.assertIn("sales team, distributors", text)
        self.assertIn("local installation or after-sales team", text)
        self.assertIn("freight forwarder or strong customs-clearance capability", text)

    def test_template_calls_out_an_ambiguous_form_answer(self):
        text = render_first_message(
            fake_lead(purchase_purpose_answer="100meter by 50meter")
        )
        self.assertIn("I may have misunderstood", text)
        self.assertIn("100meter by 50meter", text)

    def test_plan_requires_approval_and_opt_in(self):
        service = LeadOutreachService(dry_run=True)
        plan = service.plan(fake_lead())
        self.assertEqual(plan.status, OutreachStatus.PENDING_APPROVAL)
        self.assertFalse(plan.would_send)

    def test_allowlist_and_simulation_never_call_sender(self):
        service = LeadOutreachService(dry_run=True, allowlist=["+15555550100"])
        plan = service.plan(fake_lead(), approved=True, opted_in=True)
        self.assertEqual(plan.status, OutreachStatus.READY_TO_SEND)
        result = service.dispatch(plan, execute=True, sender=None)
        self.assertEqual(result["status"], "dry_run")
        self.assertTrue(result["would_send"])

    def test_approved_plan_without_allowlist_is_blocked(self):
        service = LeadOutreachService(dry_run=True)
        plan = service.plan(fake_lead(), approved=True, opted_in=True)
        self.assertEqual(plan.status, OutreachStatus.BLOCKED)
        self.assertFalse(plan.would_send)

    def test_historical_customer_added_row_is_skipped(self):
        service = LeadOutreachService(
            dry_run=True,
            allowlist=["+15555550100"],
        )
        plan = service.plan(
            fake_lead(customer_added=True), approved=True, opted_in=True
        )
        self.assertEqual(plan.status, OutreachStatus.SKIPPED_EXISTING)
        self.assertFalse(plan.would_send)

    def test_retry_policy_does_not_retry_ambiguous_submission(self):
        service = LeadOutreachService(
            retry_policy=RetryPolicy(max_attempts=3, delays_seconds=(30, 120, 600))
        )
        ambiguous = service.retry_decision(
            attempt_number=1,
            error_kind="message_id_missing",
            now=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(ambiguous.status, OutreachStatus.SUBMITTED_UNKNOWN)
        self.assertIsNone(ambiguous.retry_at)

        transient = service.retry_decision(
            attempt_number=1,
            error_kind="timeout",
            now=datetime(2026, 9, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(transient.status, OutreachStatus.RETRY_WAITING)
        self.assertEqual(transient.retry_at.minute, 0)

    def test_executor_dry_run_does_not_touch_store_or_sender(self):
        class Store:
            def __init__(self):
                self.calls = 0

            def ensure_plan(self, *_args, **_kwargs):
                self.calls += 1

        class Never:
            def check_number(self, _phone):
                raise AssertionError("registration lookup must not run in dry-run")

            def send_text(self, _phone, _text):
                raise AssertionError("send must not run in dry-run")

        service = LeadOutreachService(
            dry_run=True,
            allowlist=["+15555550100"],
        )
        plan = service.plan(fake_lead(), approved=True, opted_in=True)
        store = Store()
        result = LeadOutreachExecutor(
            service,
            store,
            registration_checker=Never(),
            sender=Never(),
        ).run(plan, execute=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(store.calls, 0)

    def test_executor_mock_send_records_sent_only_with_message_id(self):
        class Store:
            def __init__(self):
                self.calls = []

            def ensure_plan(self, *args, **kwargs):
                self.calls.append(("ensure", args, kwargs))

            def claim_for_send(self, *, idempotency_key):
                self.calls.append(("claim", idempotency_key))
                return type("Job", (), {"attempt_count": 1})()

            def record_result(self, job, **kwargs):
                self.calls.append(("record", kwargs))
                return True

            def record_outbound_message(self, **kwargs):
                self.calls.append(("outbound", kwargs))
                return True

        class Checker:
            def check_number(self, phone):
                return {"status": "registered", "phone": phone, "chat_jid": "123@lid"}

        class Sender:
            def send_text(self, phone, text):
                return {
                    "status": "sent",
                    "phone": phone,
                    "message_id": "mock-message-id",
                    "delivery_status": "unknown",
                }

        service = LeadOutreachService(
            dry_run=False,
            real_send_enabled=True,
            allowlist=["+15555550100"],
            known_phone_checker=lambda _phone: False,
        )
        plan = service.plan(fake_lead(), approved=True, opted_in=True)
        store = Store()
        result = LeadOutreachExecutor(
            service,
            store,
            registration_checker=Checker(),
            sender=Sender(),
        ).run(plan, execute=True)
        self.assertEqual(result["status"], OutreachStatus.SENT.value)
        self.assertTrue(any(call[0] == "record" for call in store.calls))
        outbound = next(call for call in store.calls if call[0] == "outbound")
        self.assertEqual(outbound[1]["message_id"], "mock-message-id")
        self.assertEqual(outbound[1]["phone"], "+15555550100")

    def test_executor_send_exception_is_ambiguous_and_not_retryable(self):
        class Store:
            def __init__(self):
                self.recorded = None

            def ensure_plan(self, *_args, **_kwargs):
                pass

            def claim_for_send(self, *, idempotency_key):
                return type("Job", (), {"attempt_count": 1})()

            def record_result(self, _job, **kwargs):
                self.recorded = kwargs
                return True

        class Checker:
            def check_number(self, _phone):
                return {"status": "registered", "chat_jid": "123@lid"}

        class FailingSender:
            def send_text(self, _phone, _text):
                raise TimeoutError("request timed out after submission")

        service = LeadOutreachService(
            dry_run=False,
            real_send_enabled=True,
            allowlist=["+15555550100"],
            known_phone_checker=lambda _phone: False,
        )
        store = Store()
        plan = service.plan(fake_lead(), approved=True, opted_in=True)
        with self.assertRaises(TimeoutError):
            LeadOutreachExecutor(
                service,
                store,
                registration_checker=Checker(),
                sender=FailingSender(),
            ).run(plan, execute=True)
        self.assertEqual(store.recorded["status"], OutreachStatus.SUBMITTED_UNKNOWN)
        self.assertIsNone(store.recorded["retry_at"])


if __name__ == "__main__":
    unittest.main()
