"""MySQL storage for the current Facebook lead form."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

import mysql.connector
from mysql.connector import Error as MySQLError

from ..config import AppSettings
from ..inbound import normalize_phone


# Exact 24-column order of the current Facebook export.
FORM_FIELD_COUNT = 24


@dataclass(frozen=True, slots=True)
class LeadRecord:
    source_lead_id: str
    created_time: datetime | None
    ad_id: str | None
    ad_name: str | None
    adset_id: str | None
    adset_name: str | None
    campaign_id: str | None
    campaign_name: str | None
    form_id: str | None
    form_name: str | None
    is_organic: bool
    platform: str | None
    business_role_answer: str | None
    target_country_answer: str | None
    coverage_area_answer: str | None
    purchase_purpose_answer: str | None
    frequency_budget_quantity_answer: str | None
    email: str | None
    whatsapp_number: str | None
    full_name: str | None
    company_name: str | None
    website: str | None
    job_title: str | None
    lead_status: str | None
    raw_data: dict[str, object]
    # This is read from MySQL for planning.  New sheet imports always start as
    # false; the historical baseline script is the only supported writer for
    # the compatibility marker.
    customer_added: bool = False

    @staticmethod
    def _clean(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _phone(value: object) -> str | None:
        # The export's WhatsApp column is expected to contain an international
        # number, but some CSV exports omit the leading ``+``.  Preserve the
        # old import behavior for 6–15 bare digits; the outreach gate still
        # requires an explicit allowlist before any send.
        return normalize_phone(value, allow_bare=True)

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        text = LeadRecord._clean(value)
        if text is None:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _bool(value: object) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    @classmethod
    def from_sheet_row(cls, headers: list[str], values: list[object]) -> LeadRecord | None:
        if len(headers) < FORM_FIELD_COUNT:
            raise ValueError(
                f"Expected at least {FORM_FIELD_COUNT} form columns, got {len(headers)}"
            )

        def value(index: int) -> object | None:
            return values[index] if index < len(values) else None

        source_id = cls._clean(value(0))
        if source_id is None:
            return None
        return cls(
            source_lead_id=source_id,
            created_time=cls._datetime(value(1)),
            ad_id=cls._clean(value(2)),
            ad_name=cls._clean(value(3)),
            adset_id=cls._clean(value(4)),
            adset_name=cls._clean(value(5)),
            campaign_id=cls._clean(value(6)),
            campaign_name=cls._clean(value(7)),
            form_id=cls._clean(value(8)),
            form_name=cls._clean(value(9)),
            is_organic=cls._bool(value(10)),
            platform=cls._clean(value(11)),
            business_role_answer=cls._clean(value(12)),
            target_country_answer=cls._clean(value(13)),
            coverage_area_answer=cls._clean(value(14)),
            purchase_purpose_answer=cls._clean(value(15)),
            frequency_budget_quantity_answer=cls._clean(value(16)),
            email=cls._clean(value(17)),
            whatsapp_number=cls._phone(value(18)),
            full_name=cls._clean(value(19)),
            company_name=cls._clean(value(20)),
            website=cls._clean(value(21)),
            job_title=cls._clean(value(22)),
            lead_status=cls._clean(value(23)),
            raw_data={"headers": headers, "values": values},
        )

    @classmethod
    def from_db_row(cls, row: Mapping[str, Any]) -> LeadRecord:
        """Convert a customer_leads row without exposing it in logs."""

        raw_data = row.get("raw_data")
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except (TypeError, ValueError):
                raw_data = {}
        if not isinstance(raw_data, dict):
            raw_data = {}

        return cls(
            source_lead_id=cls._clean(row.get("source_lead_id")) or "",
            created_time=cls._datetime(row.get("created_time")),
            ad_id=cls._clean(row.get("ad_id")),
            ad_name=cls._clean(row.get("ad_name")),
            adset_id=cls._clean(row.get("adset_id")),
            adset_name=cls._clean(row.get("adset_name")),
            campaign_id=cls._clean(row.get("campaign_id")),
            campaign_name=cls._clean(row.get("campaign_name")),
            form_id=cls._clean(row.get("form_id")),
            form_name=cls._clean(row.get("form_name")),
            is_organic=cls._bool(row.get("is_organic")),
            platform=cls._clean(row.get("platform")),
            business_role_answer=cls._clean(row.get("business_role_answer")),
            target_country_answer=cls._clean(row.get("target_country_answer")),
            coverage_area_answer=cls._clean(row.get("coverage_area_answer")),
            purchase_purpose_answer=cls._clean(row.get("purchase_purpose_answer")),
            frequency_budget_quantity_answer=cls._clean(
                row.get("frequency_budget_quantity_answer")
            ),
            email=cls._clean(row.get("email")),
            whatsapp_number=cls._phone(row.get("whatsapp_number")),
            full_name=cls._clean(row.get("full_name")),
            company_name=cls._clean(row.get("company_name")),
            website=cls._clean(row.get("website")),
            job_title=cls._clean(row.get("job_title")),
            lead_status=cls._clean(row.get("lead_status")),
            raw_data=raw_data,
            customer_added=cls._bool(row.get("customer_added")),
        )

    def as_params(self) -> tuple[object, ...]:
        return (
            self.source_lead_id,
            self.created_time,
            self.ad_id,
            self.ad_name,
            self.adset_id,
            self.adset_name,
            self.campaign_id,
            self.campaign_name,
            self.form_id,
            self.form_name,
            self.is_organic,
            self.platform,
            self.business_role_answer,
            self.target_country_answer,
            self.coverage_area_answer,
            self.purchase_purpose_answer,
            self.frequency_budget_quantity_answer,
            self.email,
            self.whatsapp_number,
            self.full_name,
            self.company_name,
            self.website,
            self.job_title,
            self.lead_status,
            False,
            json.dumps(self.raw_data, ensure_ascii=False),
        )


@dataclass(frozen=True, slots=True)
class LeadSyncStats:
    inserted: int
    skipped: int


class LeadRepository:
    """Append-only repository for the current Facebook lead form."""

    _INSERT_SQL = """
        INSERT IGNORE INTO customer_leads (
            source_lead_id, created_time, ad_id, ad_name, adset_id, adset_name,
            campaign_id, campaign_name, form_id, form_name, is_organic, platform,
            business_role_answer, target_country_answer, coverage_area_answer,
            purchase_purpose_answer, frequency_budget_quantity_answer, email,
            whatsapp_number, full_name, company_name, website, job_title,
            lead_status, customer_added, raw_data
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    _LEAD_COLUMNS = """
        l.source_lead_id, l.created_time, l.ad_id, l.ad_name, l.adset_id,
        l.adset_name, l.campaign_id, l.campaign_name, l.form_id, l.form_name,
        l.is_organic, l.platform, l.business_role_answer,
        l.target_country_answer, l.coverage_area_answer,
        l.purchase_purpose_answer, l.frequency_budget_quantity_answer,
        l.email, l.whatsapp_number, l.full_name, l.company_name, l.website,
        l.job_title, l.lead_status, l.customer_added, l.raw_data
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._connect = connection_factory or (
            lambda: mysql.connector.connect(**settings.mysql_connect_kwargs())
        )

    def append(self, leads: Iterable[LeadRecord]) -> LeadSyncStats:
        connection = cursor = None
        inserted = skipped = 0
        try:
            connection = self._connect()
            connection.start_transaction()
            cursor = connection.cursor()
            for lead in leads:
                cursor.execute(self._INSERT_SQL, lead.as_params())
                if cursor.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            connection.commit()
            return LeadSyncStats(inserted=inserted, skipped=skipped)
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise RuntimeError("MySQL lead append failed") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def find_unplanned_outreach_leads(self, *, limit: int = 100) -> list[LeadRecord]:
        """Read new leads that do not yet have an outreach plan.

        The anti-join makes repeated polling cheap.  The unique key on
        ``lead_outreach`` remains the final race-safe idempotency boundary when
        two planner processes observe the same row concurrently.
        """

        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                f"""
                SELECT {self._LEAD_COLUMNS}
                FROM customer_leads AS l
                LEFT JOIN lead_outreach AS o
                  ON o.source_lead_id = l.source_lead_id
                WHERE (l.customer_added = FALSE OR l.customer_added IS NULL)
                  AND o.source_lead_id IS NULL
                ORDER BY COALESCE(l.created_time, l.imported_at), l.source_lead_id
                LIMIT %s
                """,
                (limit,),
            )
            return [LeadRecord.from_db_row(row) for row in cursor.fetchall()]
        except (MySQLError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("MySQL pending lead query failed") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def get_by_source_lead_id(self, source_lead_id: str) -> LeadRecord | None:
        """Read one lead for a separately approved outreach job."""

        source_id = str(source_lead_id).strip()
        if not source_id:
            raise ValueError("source_lead_id is required")
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                f"""
                SELECT {self._LEAD_COLUMNS}
                FROM customer_leads AS l
                WHERE l.source_lead_id = %s
                LIMIT 1
                """,
                (source_id,),
            )
            row = cursor.fetchone()
            return LeadRecord.from_db_row(row) if row else None
        except (MySQLError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("MySQL lead lookup failed") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
