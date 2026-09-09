"""Planning and safety state machine for proactive lead contact.

Phase 3 intentionally stops at planning/simulation.  The service can render a
message, validate a lead, and calculate a retry transition, but it has no
default path that calls WhatsApp.  A later send phase can provide a sender and
explicitly opt in to execution after the allowlist and approval gates pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Protocol

from ..conversation import (
    clean_questions,
    form_answer_lines,
    positive_questions_for,
    unclear_form_answers,
)
from ..inbound import normalize_phone

if TYPE_CHECKING:
    from ..persistence.lead_repository import LeadRecord


class OutreachStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    CHECKING_REGISTRATION = "checking_registration"
    INVALID_PHONE = "invalid_phone"
    NOT_REGISTERED = "not_registered"
    READY_TO_SEND = "ready_to_send"
    SENDING = "sending"
    SUBMITTED_UNKNOWN = "submitted_unknown"
    SENT = "sent"
    SEND_FAILED = "send_failed"
    RETRY_WAITING = "retry_waiting"
    OPTED_OUT = "opted_out"
    BLOCKED = "blocked"
    SKIPPED_EXISTING = "skipped_existing"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Conservative automatic retry policy for transient failures."""

    max_attempts: int = 3
    delays_seconds: tuple[int, ...] = (30, 120, 600)
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if any(delay <= 0 for delay in self.delays_seconds):
            raise ValueError("retry delays must be positive")

    def next_delay(self, attempt_number: int) -> int | None:
        """Return a delay after a failed attempt, or ``None`` when exhausted."""

        if not self.enabled or attempt_number >= self.max_attempts:
            return None
        if not self.delays_seconds:
            return 0
        index = min(max(attempt_number - 1, 0), len(self.delays_seconds) - 1)
        return self.delays_seconds[index]


@dataclass(frozen=True, slots=True)
class OutreachPlan:
    source_lead_id: str
    idempotency_key: str
    status: OutreachStatus
    phone_e164: str | None
    message_text: str
    message_text_hash: str
    reason: str | None = None
    would_send: bool = False
    template_version: str = "en-v1"
    customer_name: str | None = None


@dataclass(frozen=True, slots=True)
class RetryDecision:
    status: OutreachStatus
    retry_at: datetime | None
    reason: str


class OutreachSender(Protocol):
    def send_text(self, phone: str, text: str) -> Mapping[str, Any]: ...


class OutreachRegistrationChecker(Protocol):
    def check_number(self, phone: str) -> Mapping[str, Any]: ...


class OutreachJobStore(Protocol):
    def ensure_plan(self, plan: OutreachPlan, *, status: OutreachStatus | None = None) -> None: ...

    def list_ready_jobs(self, *, limit: int = 100): ...

    def claim_for_send(self, *, idempotency_key: str): ...

    def mark_sending(self, job) -> bool: ...

    def record_result(
        self,
        job,
        *,
        status: OutreachStatus,
        message_id: str | None = None,
        chat_jid: str | None = None,
        error: str | None = None,
        retry_at: datetime | None = None,
        event: Mapping[str, Any] | None = None,
    ) -> bool: ...

    def record_outbound_message(
        self,
        *,
        phone: str,
        customer_name: str | None,
        text: str,
        message_id: str,
        chat_jid: str | None = None,
    ) -> bool: ...


class LeadOutreachSource(Protocol):
    """Read-only source used by the planner's polling loop."""

    def find_unplanned_outreach_leads(self, *, limit: int = 100): ...

    def get_by_source_lead_id(self, source_lead_id: str): ...


@dataclass(frozen=True, slots=True)
class OutreachPlanRun:
    """Result of one lead-planning pass.

    The plans are returned to callers for dry-run inspection.  This object
    intentionally contains no aggregate phone list or message body suitable
    for logging production data.
    """

    plans: tuple[OutreachPlan, ...]
    persisted: int
    dry_run: bool

    @property
    def scanned(self) -> int:
        return len(self.plans)

    @property
    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for plan in self.plans:
            counts[plan.status.value] = counts.get(plan.status.value, 0) + 1
        return counts


def _value(lead: LeadRecord | Mapping[str, Any], name: str) -> Any:
    if isinstance(lead, Mapping):
        return lead.get(name)
    return getattr(lead, name, None)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _is_true(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _known_phone(
    checker: Callable[..., bool] | None,
    phone: str,
    *,
    source_lead_id: str | None = None,
) -> bool:
    """Call old one-argument checkers and new source-aware checkers alike."""

    if checker is None:
        return False
    try:
        return bool(checker(phone, exclude_source_lead_id=source_lead_id))
    except TypeError as exc:
        # Keep the small callable protocol used by phase-3 tests compatible.
        # Only fall back when the callable rejects the new keyword itself.
        if "exclude_source_lead_id" not in str(exc):
            raise
        return bool(checker(phone))


def render_first_message(
    lead: LeadRecord | Mapping[str, Any],
    *,
    custom_questions: Iterable[str] | None = None,
) -> str:
    """Render the English first-contact template from form data.

    When no custom questions are supplied, the first three questions are
    selected from the scoring rubric for this lead's conservative category
    hint.  They are only fact-finding prompts; the later scoring turn must
    still verify the customer's answers before awarding points.
    """

    questions = clean_questions(custom_questions)
    if not questions:
        questions = [question.text for question in positive_questions_for(lead, limit=3)]

    channel = _text(_value(lead, "platform")) or "the channel where you contacted us"
    channel = {"fb": "Facebook", "facebook": "Facebook", "ig": "Instagram", "instagram": "Instagram"}.get(
        channel.lower(), channel
    )
    country = _text(_value(lead, "target_country_answer"))
    area = _text(_value(lead, "coverage_area_answer"))
    scenario = area or "your project"
    answers = form_answer_lines(lead)
    lines = [
        "Hi, this is Luna. We are a factory specializing in mobile signal booster systems and high-power signal amplifiers.",
        f"I saw your inquiry on {channel}. Do you have a project that needs signal coverage in {scenario}?",
    ]
    if answers:
        lines.extend(["You provided the following answers in the form:", *answers])
        lines.append("Could you please confirm whether any of the information above is incorrect?")
        unclear = unclear_form_answers(lead)
        if unclear:
            label, value = unclear[0]
            lines.append(
                f'I may have misunderstood "{label}": "{value}". '
                "Could you please clarify this answer?"
            )
    lines.append(
        "Could you also share a few more details about your project? "
        "I will ask up to three questions at a time. After you reply, "
        "I will continue with any remaining questions."
    )
    if questions:
        lines.extend(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    lines.append(
        f"Once I have these details, I can recommend suitable models for {country or 'your country/region'}."
    )
    return "\n\n".join(lines)


def outreach_idempotency_key(source_lead_id: str, template_version: str = "en-v1") -> str:
    return hashlib.sha256(
        f"lead:{source_lead_id}\x1ftemplate:{template_version}".encode("utf-8")
    ).hexdigest()


class LeadOutreachService:
    """Create safe plans and simulations for one lead at a time."""

    def __init__(
        self,
        *,
        template_version: str = "en-v1",
        dry_run: bool = True,
        real_send_enabled: bool = False,
        allowlist: Iterable[str] = (),
        known_phone_checker: Callable[..., bool] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.template_version = template_version
        self.dry_run = dry_run
        self.real_send_enabled = real_send_enabled
        self.allowlist = frozenset(
            phone for phone in (normalize_phone(item) for item in allowlist) if phone
        )
        self.known_phone_checker = known_phone_checker
        self.retry_policy = retry_policy or RetryPolicy()

    def plan(
        self,
        lead: LeadRecord | Mapping[str, Any],
        *,
        approved: bool = False,
        opted_in: bool = False,
        custom_questions: Iterable[str] = (),
    ) -> OutreachPlan:
        source_lead_id = _text(_value(lead, "source_lead_id"))
        if not source_lead_id:
            raise ValueError("source_lead_id is required")
        phone = normalize_phone(_value(lead, "whatsapp_number") or _value(lead, "phone_e164"))
        text = render_first_message(lead, custom_questions=custom_questions)
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        key = outreach_idempotency_key(source_lead_id, self.template_version)
        customer_name = _text(_value(lead, "full_name")) or _text(
            _value(lead, "company_name")
        )

        # ``customer_added`` is only a historical compatibility marker.  A
        # true value means this row is outside the automatic outreach set; it
        # never means that a message was sent.
        if _is_true(_value(lead, "customer_added")):
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.SKIPPED_EXISTING,
                phone,
                text,
                text_hash,
                reason="lead is marked as historical baseline; no send is implied",
                template_version=self.template_version,
                customer_name=customer_name,
            )

        if phone is None:
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.INVALID_PHONE,
                None,
                text,
                text_hash,
                reason="lead has no unambiguous international phone number",
                template_version=self.template_version,
                customer_name=customer_name,
            )
        if not opted_in or not approved:
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.PENDING_APPROVAL,
                phone,
                text,
                text_hash,
                reason="explicit opt-in and approval are required",
                template_version=self.template_version,
                customer_name=customer_name,
            )
        if _known_phone(
            self.known_phone_checker,
            phone,
            source_lead_id=source_lead_id,
        ):
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.BLOCKED,
                phone,
                text,
                text_hash,
                reason="phone overlaps an existing customer/lead safety set",
                template_version=self.template_version,
                customer_name=customer_name,
            )
        if not self.allowlist:
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.BLOCKED,
                phone,
                text,
                text_hash,
                reason="an explicit per-number allowlist is required",
                template_version=self.template_version,
                customer_name=customer_name,
            )
        if phone not in self.allowlist:
            return OutreachPlan(
                source_lead_id,
                key,
                OutreachStatus.BLOCKED,
                phone,
                text,
                text_hash,
                reason="phone is not in the explicit allowlist",
                template_version=self.template_version,
                customer_name=customer_name,
            )

        return OutreachPlan(
            source_lead_id,
            key,
            OutreachStatus.READY_TO_SEND,
            phone,
            text,
            text_hash,
            reason="dry-run plan; no WhatsApp call was made" if self.dry_run else None,
            would_send=True,
            template_version=self.template_version,
            customer_name=customer_name,
        )

    def simulate(self, plan: OutreachPlan) -> dict[str, Any]:
        """Return a send-shaped result without invoking any sender."""

        return {
            "status": "dry_run",
            "source_lead_id": plan.source_lead_id,
            "idempotency_key": plan.idempotency_key,
            "phone": plan.phone_e164,
            "message_text_hash": plan.message_text_hash,
            "template_version": plan.template_version,
            "would_send": plan.would_send,
            "customer_name": plan.customer_name,
            "reason": plan.reason,
        }

    def dispatch(
        self,
        plan: OutreachPlan,
        *,
        sender: OutreachSender | None = None,
        execute: bool = False,
    ) -> Mapping[str, Any]:
        """Dispatch only when every explicit phase gate is enabled.

        ``execute`` is deliberately false by default.  Even with a sender
        object supplied, phase-3 code can therefore only return a simulation.
        """

        if not execute or self.dry_run or not self.real_send_enabled:
            return self.simulate(plan)
        if plan.status is not OutreachStatus.READY_TO_SEND or not plan.phone_e164:
            raise ValueError(f"plan is not sendable: {plan.status.value}")
        if self.known_phone_checker is None:
            raise ValueError("a production phone safety checker is required")
        if _known_phone(
            self.known_phone_checker,
            plan.phone_e164,
            source_lead_id=plan.source_lead_id,
        ):
            raise ValueError("phone is present in the production safety set")
        if sender is None:
            raise ValueError("a sender is required when execute=True")
        return sender.send_text(plan.phone_e164, plan.message_text)

    def retry_decision(
        self,
        *,
        attempt_number: int,
        error_kind: str,
        now: datetime | None = None,
    ) -> RetryDecision:
        """Classify an error without retrying ambiguous submissions.

        A timeout after WhatsApp may have accepted the message.  Such an event
        is kept as ``submitted_unknown`` and requires reconciliation rather
        than an automatic duplicate send.
        """

        normalized = error_kind.strip().lower()
        if normalized in {"submitted_unknown", "unknown_submission", "message_id_missing"}:
            return RetryDecision(
                OutreachStatus.SUBMITTED_UNKNOWN,
                None,
                "send result is ambiguous; reconcile before retrying",
            )
        transient = normalized in {
            "timeout",
            "network",
            "disconnected",
            "not_ready",
            "rate_limited",
            "temporary",
            "502",
            "503",
            "429",
        }
        if not transient:
            return RetryDecision(
                OutreachStatus.SEND_FAILED,
                None,
                f"non-retryable error: {error_kind}",
            )
        delay = self.retry_policy.next_delay(attempt_number)
        if delay is None:
            return RetryDecision(
                OutreachStatus.SEND_FAILED,
                None,
                "retry limit reached",
            )
        base = now or datetime.now(timezone.utc)
        return RetryDecision(
            OutreachStatus.RETRY_WAITING,
            base + timedelta(seconds=delay),
            f"retrying after {delay} seconds",
        )


class LeadOutreachPlanner:
    """Detect new database leads and create idempotent outreach plans.

    Planning is separate from sending.  A dry-run reads the source and renders
    statuses but does not write ``lead_outreach``.  The write-enabled mode only
    creates/updates the durable plan row; it never calls WhatsApp.
    """

    def __init__(
        self,
        source: LeadOutreachSource,
        store: OutreachJobStore,
        service: LeadOutreachService,
    ) -> None:
        self.source = source
        self.store = store
        self.service = service

    def run_once(
        self,
        *,
        limit: int = 100,
        approved: bool = False,
        opted_in: bool = False,
        custom_questions: Iterable[str] = (),
        dry_run: bool = True,
    ) -> OutreachPlanRun:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        leads = self.source.find_unplanned_outreach_leads(limit=limit)
        plans: list[OutreachPlan] = []
        persisted = 0
        for lead in leads:
            plan = self.service.plan(
                lead,
                approved=approved,
                opted_in=opted_in,
                custom_questions=custom_questions,
            )
            plans.append(plan)
            if not dry_run:
                self.store.ensure_plan(plan)
                persisted += 1
        return OutreachPlanRun(tuple(plans), persisted, dry_run)


@dataclass(frozen=True, slots=True)
class OutreachWorkerRun:
    """Aggregate result for one ready-job polling pass."""

    checked: int
    results: tuple[Mapping[str, Any], ...]
    dry_run: bool


class LeadOutreachWorker:
    """Process already approved plans through the separately gated executor."""

    def __init__(
        self,
        source: LeadOutreachSource,
        store: OutreachJobStore,
        service: LeadOutreachService,
        executor: "LeadOutreachExecutor",
    ) -> None:
        self.source = source
        self.store = store
        self.service = service
        self.executor = executor

    def run_once(self, *, limit: int = 20, dry_run: bool = True) -> OutreachWorkerRun:
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        jobs = self.store.list_ready_jobs(limit=limit)
        results: list[Mapping[str, Any]] = []
        for job in jobs:
            lead = self.source.get_by_source_lead_id(job.source_lead_id)
            if lead is None:
                results.append(
                    {
                        "status": "missing_lead",
                        "source_lead_id": job.source_lead_id,
                    }
                )
                continue
            # A ready row is the durable record of prior approval and opt-in.
            # The service still re-runs the allowlist and production phone check
            # before an executor can send anything.
            plan = self.service.plan(lead, approved=True, opted_in=True)
            results.append(self.executor.run(plan, execute=not dry_run))
        return OutreachWorkerRun(len(jobs), tuple(results), dry_run)


class LeadOutreachExecutor:
    """Execute one approved plan behind an explicit phase-5 gate.

    The executor is provided now so the future one-number test can traverse
    the real state machine.  It cannot run in phase 3 accidentally: callers
    must set both ``execute=True`` and ``LeadOutreachService.real_send_enabled``
    (which defaults to false), and the plan itself must have passed approval,
    opt-in, allowlist, and production safety checks.
    """

    def __init__(
        self,
        service: LeadOutreachService,
        store: OutreachJobStore,
        *,
        registration_checker: OutreachRegistrationChecker,
        sender: OutreachSender,
    ) -> None:
        self.service = service
        self.store = store
        self.registration_checker = registration_checker
        self.sender = sender

    def run(self, plan: OutreachPlan, *, execute: bool = False) -> Mapping[str, Any]:
        if not execute or self.service.dry_run or not self.service.real_send_enabled:
            return self.service.simulate(plan)
        if plan.status is not OutreachStatus.READY_TO_SEND or not plan.phone_e164:
            return {
                "status": plan.status.value,
                "source_lead_id": plan.source_lead_id,
                "idempotency_key": plan.idempotency_key,
                "would_send": False,
                "reason": plan.reason,
            }

        self.store.ensure_plan(plan)
        job = self.store.claim_for_send(idempotency_key=plan.idempotency_key)
        if job is None:
            return {
                "status": "already_claimed_or_completed",
                "source_lead_id": plan.source_lead_id,
                "idempotency_key": plan.idempotency_key,
            }

        send_started = False
        chat_jid: str | None = None
        try:
            registration = self.registration_checker.check_number(plan.phone_e164)
            if registration.get("status") != "registered" or not registration.get("chat_jid"):
                self.store.record_result(
                    job,
                    status=OutreachStatus.NOT_REGISTERED,
                    error="phone is not registered on WhatsApp",
                    event={"registration": dict(registration)},
                )
                return {
                    "status": OutreachStatus.NOT_REGISTERED.value,
                    "source_lead_id": plan.source_lead_id,
                    "phone": plan.phone_e164,
                }

            chat_jid = str(registration["chat_jid"])

            mark_sending = getattr(self.store, "mark_sending", None)
            if callable(mark_sending) and not mark_sending(job):
                raise RuntimeError("outreach lease was lost before sending")

            send_started = True
            send_result = self.sender.send_text(plan.phone_e164, plan.message_text)
            message_id = send_result.get("message_id")
            operation_status = str(send_result.get("status") or "")
            final_status = (
                OutreachStatus.SENT
                if operation_status == "sent" and message_id
                else OutreachStatus.SUBMITTED_UNKNOWN
            )
            self.store.record_result(
                job,
                status=final_status,
                message_id=message_id,
                chat_jid=chat_jid,
                error=(
                    None
                    if final_status is OutreachStatus.SENT
                    else "send operation returned no reliable message id"
                ),
                event={"registration": dict(registration), "send": dict(send_result)},
            )
            history_recorded: bool | None = None
            history_record_error: str | None = None
            if final_status is OutreachStatus.SENT and message_id:
                record_message = getattr(self.store, "record_outbound_message", None)
                if callable(record_message):
                    try:
                        history_recorded = bool(
                            record_message(
                                phone=plan.phone_e164,
                                customer_name=plan.customer_name,
                                text=plan.message_text,
                                message_id=str(message_id),
                                chat_jid=chat_jid,
                            )
                        )
                    except Exception as exc:  # pragma: no cover - connector-specific
                        # The send is already committed by WhatsApp.  Surface a
                        # history-write failure without making a duplicate send
                        # eligible for retry.
                        history_record_error = str(exc)
            return {
                "status": final_status.value,
                "source_lead_id": plan.source_lead_id,
                "phone": plan.phone_e164,
                "message_id": message_id,
                "delivery_status": send_result.get("delivery_status", "unknown"),
                **(
                    {"history_recorded": history_recorded}
                    if history_recorded is not None
                    else {}
                ),
                **(
                    {"history_record_error": history_record_error}
                    if history_record_error
                    else {}
                ),
            }
        except Exception as exc:
            # Once the send request has started, even an HTTP error/timeout is
            # potentially ambiguous.  Never blind-retry that case.
            if send_started:
                error_kind = "submitted_unknown"
            else:
                error_kind = (
                    getattr(exc, "error_kind", None)
                    or getattr(exc, "code", None)
                    or getattr(exc, "status_code", None)
                    or getattr(exc, "statusCode", None)
                    or "temporary"
                )
            decision = self.service.retry_decision(
                attempt_number=max(int(getattr(job, "attempt_count", 1)), 1),
                error_kind=str(error_kind),
            )
            self.store.record_result(
                job,
                status=decision.status,
                chat_jid=chat_jid,
                error=str(exc),
                retry_at=decision.retry_at,
                event={"error_kind": str(error_kind)},
            )
            raise
