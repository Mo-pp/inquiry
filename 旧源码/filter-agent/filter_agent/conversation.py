"""Lead conversation context and the first follow-up question set.

The lead form is a useful starting point, not proof that every answer is
correct.  This module keeps the small amount of deterministic workflow logic
outside the language-model prompt: it identifies a conservative customer-type
hint and provides at most three English questions for the next round.  The
model still decides whether a customer has actually confirmed a fact and
which scoring rules are earned.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class PositiveQuestion:
    """One customer-facing question tied to a scoring rule."""

    code: str
    text: str
    points: int


FORM_FIELDS: tuple[tuple[str, str], ...] = (
    ("Customer or project category", "business_role_answer"),
    ("Country where the signal booster will be used", "target_country_answer"),
    ("Coverage area", "coverage_area_answer"),
    ("Intended use (home use or resale)", "purchase_purpose_answer"),
    ("Required frequency band, budget, and quantity", "frequency_budget_quantity_answer"),
)


# These are prompts for collecting facts, not automatic score assignments.  A
# score is earned only after the customer explicitly confirms the fact.
POSITIVE_QUESTIONS: dict[str, tuple[PositiveQuestion, ...]] = {
    "A": (
        PositiveQuestion("A_PROJECT_SCOPE", "Is this for a public, multi-area, or additional project?", 10),
        PositiveQuestion("A_TECHNICAL_MATERIAL", "Can you share a drawing, technical parameters, or site materials?", 10),
        PositiveQuestion("A_DEADLINE", "Do you have a required delivery or project deadline?", 10),
        PositiveQuestion("A_ENGINEER_CARD", "Could you share an engineering business card or company profile?", 5),
        PositiveQuestion("A_QUALITY_PRIORITY", "Is stable performance and technical capability more important than the lowest price?", 5),
        PositiveQuestion("A_VENDOR_EXPERIENCE", "Would supplier expertise and completed project references be important for this project?", 5),
        PositiveQuestion("A_CUSTOM_SPEC", "Do you need a custom frequency, power, or host-unit specification?", 5),
    ),
    "B": (
        PositiveQuestion("B_CHANNELS", "Do you have a sales team, distributors, or online/offline sales channels?", 7),
        PositiveQuestion("B_FREIGHT", "Do you have a freight forwarder or strong customs-clearance capability in your country?", 7),
        PositiveQuestion("B_AFTERSALES", "Do you have a local installation or after-sales team?", 7),
        PositiveQuestion("B_QUANTITY", "What quantity do you normally purchase for one model or one order?", 4),
        PositiveQuestion("B_BRAND", "Are you considering your own brand or customized packaging?", 4),
        PositiveQuestion("B_INDUSTRY", "Are your main products in communications, low-voltage systems, or electronics?", 4),
        PositiveQuestion("B_DECISION", "Are you the owner or the person who makes the purchasing decision?", 4),
        PositiveQuestion("B_PLAN", "Do you have a clear annual or staged purchasing plan?", 3),
        PositiveQuestion("B_EXPERIENCE", "Have you worked with signal boosters and the local market before?", 2),
    ),
    "C": (
        PositiveQuestion("C_OPERATOR_PROJECT", "Are you an operator or integrator with a confirmed cooperation project?", 10),
        PositiveQuestion("C_SELECTION", "Do you need product selection, detailed parameters, or a high-end series?", 10),
        PositiveQuestion("C_REMOTE", "Would remote control be required for the high-end equipment?", 10),
        PositiveQuestion("C_TENDER", "Can you share tender documents, technical files, or project materials?", 10),
        PositiveQuestion("C_COMPLIANCE", "Do you already have local import or sales permits, or a compliance plan?", 5),
        PositiveQuestion("C_FREIGHT", "Do you have a freight forwarder or strong customs-clearance capability in China?", 5),
    ),
    "D": (
        PositiveQuestion("D_SITE_MATERIAL", "Can you share a simple drawing, photo, video, or other site material?", 10),
        PositiveQuestion("D_DECISION", "Are you the person who makes the purchasing decision?", 10),
        PositiveQuestion("D_EXPERIENCE", "Have you worked with signal booster installation or related products before?", 10),
        PositiveQuestion("D_DEADLINE", "Do you have a required delivery or installation deadline?", 10),
        PositiveQuestion("D_SIGNAL_SOURCE", "Is there a usable mobile signal source within about 2 km of the site?", 10),
    ),
    "E": (
        PositiveQuestion("E_USAGE", "Could you describe the exact areas you want to cover and the current signal problem?", 5),
        PositiveQuestion("E_MULTI_AREA", "Do you need to cover more than one area, such as a home and a farm or livestock area?", 5),
        PositiveQuestion("E_SIGNAL_SOURCE", "Is there a usable mobile signal source within about 1 km of the site?", 5),
        PositiveQuestion("E_PURCHASE_PLAN", "Are you planning to purchase in the near future?", 5),
        PositiveQuestion("E_CHINA_EXPERIENCE", "Have you bought from China or worked with a freight forwarder or customs agent before?", 5),
        PositiveQuestion("E_REFERRAL", "Were you referred to us by an existing customer or partner?", 10),
        PositiveQuestion("E_PRICE_PRIORITY", "Is the best coverage result more important to you than the lowest price?", 10),
    ),
    "K": (
        PositiveQuestion("K_USAGE", "Could you describe the exact areas you want to cover and the current signal problem?", 5),
        PositiveQuestion("K_MULTI_AREA", "Do you need to cover more than one area, such as a home and a farm or livestock area?", 5),
        PositiveQuestion("K_SIGNAL_SOURCE", "Is there a usable mobile signal source within about 1 km of the site?", 5),
        PositiveQuestion("K_PURCHASE_PLAN", "Are you planning to purchase in the near future?", 5),
        PositiveQuestion("K_CHINA_EXPERIENCE", "Have you bought from China or worked with a freight forwarder or customs agent before?", 5),
        PositiveQuestion("K_REFERRAL", "Were you referred to us by an existing customer or partner?", 10),
        PositiveQuestion("K_PRICE_PRIORITY", "Is the best coverage result more important to you than the lowest price?", 10),
    ),
}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _all_form_text(lead: Mapping[str, Any] | Any) -> str:
    values = []
    for _label, field in FORM_FIELDS:
        value = lead.get(field) if isinstance(lead, Mapping) else getattr(lead, field, None)
        if _text(value):
            values.append(_text(value).lower())
    return " ".join(values)


def parse_area_square_meters(value: Any) -> float | None:
    """Parse common form descriptions such as ``100 by 50 meter``."""

    text = _text(value).lower().replace(",", "")
    if not text:
        return None
    rectangle = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:m(?:eter|eters)?\s*)?(?:by|x|×)\s*"
        r"(\d+(?:\.\d+)?)\s*m?(?:eter|eters)?",
        text,
    )
    if rectangle:
        return float(rectangle.group(1)) * float(rectangle.group(2))
    area = re.search(r"(\d+(?:\.\d+)?)\s*(?:m2|m²|sqm|sq\.?\s*m|square\s*meters?)", text)
    return float(area.group(1)) if area else None


def infer_customer_level(lead: Mapping[str, Any] | Any) -> str:
    """Return a conservative category hint for question selection.

    This hint never changes the score.  Ambiguous leads stay ``UNKNOWN`` so
    the model can ask a clarifying question instead of assigning a category
    from a single keyword.
    """

    text = _all_form_text(lead)
    personal = any(term in text for term in ("personal", "home", "household", "family", "self use", "自用", "家用"))
    operator = any(term in text for term in ("operator", "telecom", "carrier", "integrator", "运营商", "投标"))
    wholesale = any(term in text for term in ("wholesale", "distributor", "reseller", "resale", "resell", "trade", "批发", "经销"))
    engineer = any(term in text for term in ("engineer", "contractor", "installer", "工程", "安装"))

    if personal and not operator:
        area = parse_area_square_meters(
            lead.get("coverage_area_answer") if isinstance(lead, Mapping) else getattr(lead, "coverage_area_answer", None)
        )
        return "E" if area is not None and area > 500 else "K"
    if operator:
        return "C"
    if wholesale:
        return "B"
    if engineer:
        return "D" if personal else "A"
    return "UNKNOWN"


def positive_questions_for(
    lead: Mapping[str, Any] | Any,
    *,
    limit: int = 3,
    level: str | None = None,
) -> tuple[PositiveQuestion, ...]:
    """Choose the first few fact-finding questions for a lead."""

    if not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    chosen_level = (level or infer_customer_level(lead)).upper()
    questions = POSITIVE_QUESTIONS.get(chosen_level)
    if questions is None:
        questions = (
            PositiveQuestion("UNKNOWN_USAGE", "Could you describe the exact area and current signal problem?", 0),
            PositiveQuestion("UNKNOWN_SIGNAL_SOURCE", "Is there a usable mobile signal source near the site?", 0),
            PositiveQuestion("UNKNOWN_PURCHASE_PLAN", "When are you planning to purchase?", 0),
        )
    return questions[:limit]


def clean_questions(questions: Iterable[str] | None) -> list[str]:
    """Normalize customer-facing custom questions and reject more than three."""

    cleaned = [str(question).strip() for question in (questions or ()) if str(question).strip()]
    if len(cleaned) > 3:
        raise ValueError("at most three custom questions may be sent at once")
    return cleaned


def form_answer_lines(lead: Mapping[str, Any] | Any) -> list[str]:
    """Render the five form answers in the stable order used by the template."""

    lines: list[str] = []
    for index, (label, field) in enumerate(FORM_FIELDS, start=1):
        value = lead.get(field) if isinstance(lead, Mapping) else getattr(lead, field, None)
        value_text = _text(value)
        if value_text:
            lines.append(f"{index}. {label}: {value_text}")
    return lines


def unclear_form_answers(lead: Mapping[str, Any] | Any) -> tuple[tuple[str, str], ...]:
    """Return form answers that deserve an explicit clarification question."""

    def value_for(field: str) -> str:
        value = lead.get(field) if isinstance(lead, Mapping) else getattr(lead, field, None)
        return _text(value)

    unclear: list[tuple[str, str]] = []
    for label, field in FORM_FIELDS:
        value = value_for(field)
        if not value:
            unclear.append((label, "blank"))
    purpose = value_for("purchase_purpose_answer").lower()
    if purpose and parse_area_square_meters(purpose) is not None:
        unclear.append(("Intended use (home use or resale)", value_for("purchase_purpose_answer")))
    return tuple(dict.fromkeys(unclear))


def render_lead_context(lead: Mapping[str, Any] | Any) -> str:
    """Render lead form facts for the private scheduler prompt."""

    lines = []
    for label, field in FORM_FIELDS:
        value = lead.get(field) if isinstance(lead, Mapping) else getattr(lead, field, None)
        value_text = _text(value)
        lines.append(f"- {label}: {value_text or '[blank]'}")
    return "\n".join(lines)


def render_question_candidates(lead: Mapping[str, Any] | Any, *, limit: int = 9) -> str:
    """Render private question candidates with their rule codes."""

    level = infer_customer_level(lead)
    questions = POSITIVE_QUESTIONS.get(level, positive_questions_for(lead, limit=3))
    return "\n".join(
        f"- {question.code} (+{question.points}): {question.text}"
        for question in questions[:limit]
    )


def question_codes_in_messages(
    messages: Iterable[Mapping[str, Any]], *, level: str | None = None
) -> tuple[str, ...]:
    """Find rubric questions that were already sent verbatim in chat history.

    The language model remains responsible for recognizing paraphrases and
    whether a reply actually answers a question.  Exact matching covers the
    deterministic first-contact template and gives the model a useful hint
    after a restart.
    """

    outbound_text = "\n".join(
        _text(message.get("message_text") or message.get("text"))
        for message in messages
        if str(message.get("direction") or "").lower() == "out"
    ).lower()
    if not outbound_text:
        return ()
    found: list[str] = []
    question_sets = (
        [POSITIVE_QUESTIONS.get(level.upper(), ())]
        if level and level.upper() in POSITIVE_QUESTIONS
        else POSITIVE_QUESTIONS.values()
    )
    for questions in question_sets:
        for question in questions:
            if question.text.lower() in outbound_text:
                found.append(question.code)
    return tuple(dict.fromkeys(found))


__all__ = [
    "FORM_FIELDS",
    "POSITIVE_QUESTIONS",
    "PositiveQuestion",
    "clean_questions",
    "form_answer_lines",
    "infer_customer_level",
    "parse_area_square_meters",
    "positive_questions_for",
    "render_lead_context",
    "render_question_candidates",
    "question_codes_in_messages",
    "unclear_form_answers",
]
