import unittest
from datetime import datetime

import httpx

from filter_agent import AppSettings
from filter_agent.lead_sync import GoogleSheetsConfigError, GoogleSheetsReader, LeadSyncService
from filter_agent.persistence.lead_repository import LeadRecord, LeadRepository, LeadSyncStats


HEADERS = [
    "id", "created_time", "ad_id", "ad_name", "adset_id", "adset_name",
    "campaign_id", "campaign_name", "form_id", "form_name", "is_organic",
    "platform", "do_you_fall_into_the_category？",
    "_in_which_country_you_want_to_use_the_signal_booster?",
    "2、what_is_the_square_area_of_the_signal_you_want_to_cover?_over_500_sqm_or_less_than_500_sqm",
    "_you_want_to_purchase_our_products_for_home_use_or_for_resell?",
    "4、what_signal_frequency_band_do_you_need_to_purchase?_what_is_your_budget_and_quantity?",
    "email", "whatsapp_number", "full_name", "company_name", "website", "job_title",
    "lead_status",
]


def make_values(source_id="l:1"):
    return [
        source_id, "2026-08-25T02:29:22-05:00", "ad-1", "Ad", "as-1", "Adset",
        "campaign-1", "Campaign", "form-1", "Form", "false", "fb", "buyer",
        "Nigeria", "over_500_sqm", "resell", "4G, 10 units", "name@example.com",
        "p:+63935 416 1080", "Name", "Company", "example.com", "Owner", "CREATED",
    ]


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _ValuesResource:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def get(self, **kwargs):
        self.kwargs = kwargs
        return _Request(self.result)


class _SheetsResource:
    def __init__(self, values):
        self.values_resource = _ValuesResource(values)

    def get(self, **_kwargs):
        return _Request({"sheets": [{"properties": {"sheetId": 0, "title": "工作表1"}}]})

    def values(self):
        return self.values_resource


class _Service:
    def __init__(self, values):
        self.sheets_resource = _SheetsResource(values)

    def spreadsheets(self):
        return self.sheets_resource


class _Cursor:
    def __init__(self, rowcounts):
        self.rowcounts = iter(rowcounts)
        self.executions = []
        self.rowcount = 0

    def execute(self, sql, params):
        self.executions.append((sql, params))
        self.rowcount = next(self.rowcounts)

    def close(self):
        pass


class _Connection:
    def __init__(self, rowcounts):
        self.cursor_instance = _Cursor(rowcounts)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def start_transaction(self):
        pass

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _ReadCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class _ReadConnection:
    def __init__(self, rows):
        self.cursor_instance = _ReadCursor(rows)
        self.closed = False

    def cursor(self, **_kwargs):
        return self.cursor_instance

    def close(self):
        self.closed = True


class LeadRecordTests(unittest.TestCase):
    def test_maps_current_facebook_form_by_position(self):
        lead = LeadRecord.from_sheet_row(HEADERS, make_values())
        assert lead is not None
        self.assertEqual(lead.source_lead_id, "l:1")
        self.assertEqual(lead.business_role_answer, "buyer")
        self.assertEqual(lead.target_country_answer, "Nigeria")
        self.assertEqual(lead.coverage_area_answer, "over_500_sqm")
        self.assertEqual(lead.purchase_purpose_answer, "resell")
        self.assertEqual(lead.frequency_budget_quantity_answer, "4G, 10 units")
        self.assertEqual(lead.whatsapp_number, "+639354161080")
        self.assertEqual(lead.created_time, datetime(2026, 8, 25, 7, 29, 22))

    def test_empty_source_id_is_skipped(self):
        self.assertIsNone(LeadRecord.from_sheet_row(HEADERS, make_values("")))

    def test_short_header_is_rejected(self):
        with self.assertRaises(ValueError):
            LeadRecord.from_sheet_row(HEADERS[:23], make_values()[:23])

    def test_from_db_row_preserves_customer_added_and_raw_data(self):
        lead = LeadRecord.from_db_row(
            {
                "source_lead_id": "db-1",
                "created_time": "2026-09-04T01:02:03Z",
                "whatsapp_number": "+15555550100",
                "raw_data": '{"source": "test"}',
                "customer_added": 0,
            }
        )
        self.assertEqual(lead.source_lead_id, "db-1")
        self.assertEqual(lead.created_time, datetime(2026, 9, 4, 1, 2, 3))
        self.assertFalse(lead.customer_added)
        self.assertEqual(lead.raw_data, {"source": "test"})


class SyncConfigTests(unittest.TestCase):
    def test_google_sync_is_disabled_by_default(self):
        self.assertFalse(AppSettings().google_sheets_sync_enabled)


class ReaderTests(unittest.TestCase):
    def test_reads_current_form_range(self):
        service = _Service({"values": [HEADERS, make_values()]})
        settings = AppSettings(
            google_spreadsheet_id="sheet-1",
            google_sheets_sync_enabled=True,
        )
        reader = GoogleSheetsReader(
            settings,
            service_factory=lambda _credentials: service,
            credentials_loader=lambda _settings: object(),
        )
        leads = reader.read_leads()
        self.assertEqual([lead.source_lead_id for lead in leads], ["l:1"])
        self.assertEqual(service.sheets_resource.values_resource.kwargs["range"], "'工作表1'!A:X")

    def test_apps_script_source_reads_gateway_payload(self):
        settings = AppSettings(
            google_spreadsheet_id="sheet-1",
            google_sheets_sync_enabled=True,
            google_sheets_source="apps_script",
            google_apps_script_url="https://script.google.com/macros/s/test/exec",
            google_apps_script_token="secret",
        )
        captured = {}

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True, "headers": HEADERS, "values": [make_values()]}

        original = httpx.post
        try:
            def post(url, **kwargs):
                captured["url"], captured["kwargs"] = url, kwargs
                return Response()

            httpx.post = post
            leads = GoogleSheetsReader(settings).read_leads()
        finally:
            httpx.post = original
        self.assertEqual([lead.source_lead_id for lead in leads], ["l:1"])
        self.assertEqual(captured["kwargs"]["json"], {"token": "secret", "gid": 0})

    def test_apps_script_source_requires_url_and_token(self):
        settings = AppSettings(
            google_spreadsheet_id="sheet-1",
            google_sheets_sync_enabled=True,
            google_sheets_source="apps_script",
        )
        with self.assertRaises(GoogleSheetsConfigError):
            GoogleSheetsReader(settings).read_leads()


class RepositoryTests(unittest.TestCase):
    def test_append_is_idempotent_and_never_touches_customers(self):
        connection = _Connection([1, 0])
        repository = LeadRepository(AppSettings(), connection_factory=lambda: connection)
        lead = LeadRecord.from_sheet_row(HEADERS, make_values())
        assert lead is not None
        stats = repository.append([lead, lead])
        self.assertEqual(stats, LeadSyncStats(inserted=1, skipped=1))
        self.assertTrue(connection.committed)
        sql = connection.cursor_instance.executions[0][0].lower()
        self.assertIn("customer_leads", sql)
        self.assertNotIn("customers", sql.replace("customer_leads", ""))
        self.assertFalse(connection.cursor_instance.executions[0][1][24])

    def test_find_unplanned_outreach_leads_uses_an_idempotent_anti_join(self):
        connection = _ReadConnection(
            [
                {
                    "source_lead_id": "db-1",
                    "created_time": "2026-09-04T01:02:03Z",
                    "whatsapp_number": "+15555550100",
                    "raw_data": "{}",
                    "customer_added": 0,
                }
            ]
        )
        leads = LeadRepository(
            AppSettings(), connection_factory=lambda: connection
        ).find_unplanned_outreach_leads(limit=2)
        self.assertEqual([lead.source_lead_id for lead in leads], ["db-1"])
        sql, params = connection.cursor_instance.executions[0]
        self.assertIn("LEFT JOIN lead_outreach", sql)
        self.assertIn("customer_added", sql)
        self.assertEqual(params, (2,))


class ServiceTests(unittest.TestCase):
    def test_sync_delegates_rows_to_repository(self):
        lead = LeadRecord.from_sheet_row(HEADERS, make_values())
        assert lead is not None

        class Reader:
            def read_leads(self):
                return [lead]

        class Repository:
            def __init__(self):
                self.received = None

            def append(self, leads):
                self.received = list(leads)
                return LeadSyncStats(inserted=1, skipped=0)

        repository = Repository()
        stats = LeadSyncService(Reader(), repository, enabled=True).sync_once()
        self.assertEqual(stats.inserted, 1)
        self.assertEqual(repository.received, [lead])

    def test_disabled_sync_does_not_read_or_append(self):
        class Reader:
            def read_leads(self):
                raise AssertionError("reader must not be called while disabled")

        class Repository:
            def append(self, _leads):
                raise AssertionError("repository must not be called while disabled")

        stats = LeadSyncService(Reader(), Repository(), enabled=False).sync_once()
        self.assertEqual(stats, LeadSyncStats(inserted=0, skipped=0))

    def test_reader_gate_cannot_be_bypassed_by_service_override(self):
        settings = AppSettings(google_spreadsheet_id="sheet-1")

        class Reader:
            def __init__(self):
                self.settings = settings

            def read_leads(self):
                raise AssertionError("reader must not be called while setting is disabled")

        class Repository:
            def append(self, _leads):
                raise AssertionError("repository must not be called while setting is disabled")

        stats = LeadSyncService(Reader(), Repository(), enabled=True).sync_once()
        self.assertEqual(stats, LeadSyncStats(inserted=0, skipped=0))

    def test_disabled_run_forever_exits_without_polling(self):
        class Reader:
            def read_leads(self):
                raise AssertionError("reader must not be called while disabled")

        class Repository:
            def append(self, _leads):
                raise AssertionError("repository must not be called while disabled")

        LeadSyncService(Reader(), Repository(), enabled=False).run_forever(1)


class DisabledReaderTests(unittest.TestCase):
    def test_disabled_reader_does_not_create_google_service(self):
        settings = AppSettings(google_spreadsheet_id="sheet-1")
        reader = GoogleSheetsReader(
            settings,
            service_factory=lambda _credentials: (_ for _ in ()).throw(
                AssertionError("Google service must not be created while disabled")
            ),
            credentials_loader=lambda _settings: (_ for _ in ()).throw(
                AssertionError("credentials must not be loaded while disabled")
            ),
        )
        self.assertEqual(reader.read_leads(), [])


if __name__ == "__main__":
    unittest.main()
