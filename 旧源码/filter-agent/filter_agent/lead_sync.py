"""Google Sheets API based lead synchronizer.

Run once with ``python -m filter_agent.lead_sync --once`` or keep a local
worker alive with ``python -m filter_agent.lead_sync``. The worker only writes
to ``customer_leads``; it never reads or writes the chat customer tables.
Production synchronization is opt-in and remains disabled unless
``GOOGLE_SHEETS_SYNC_ENABLED=true`` is explicitly configured.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import logging
from pathlib import Path
import time
from typing import Any

import httpx

from .config import AppSettings
from .persistence.lead_repository import LeadRecord, LeadRepository, LeadSyncStats

LOGGER = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


class GoogleSheetsConfigError(RuntimeError):
    """Raised when Google API credentials or spreadsheet configuration is missing."""


def load_google_credentials(settings: AppSettings) -> Any:
    """Load, refresh, or interactively create the local OAuth token."""

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleSheetsConfigError(
            "Google Sheets dependencies are missing; install requirements.txt"
        ) from exc

    token_path = Path(settings.google_token_file)
    credentials_path = Path(settings.google_credentials_file)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    elif not credentials or not credentials.valid:
        if not credentials_path.exists():
            raise GoogleSheetsConfigError(
                f"OAuth client file not found: {credentials_path}. "
                "Download a Desktop OAuth client from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


class GoogleSheetsReader:
    """Read the first row as headers and convert subsequent rows to leads."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        service_factory: Callable[[Any], Any] | None = None,
        credentials_loader: Callable[[AppSettings], Any] = load_google_credentials,
    ) -> None:
        self.settings = settings
        self._service_factory = service_factory
        self._credentials_loader = credentials_loader

    def _service(self) -> Any:
        credentials = self._credentials_loader(self.settings)
        if self._service_factory is not None:
            return self._service_factory(credentials)
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GoogleSheetsConfigError(
                "Google Sheets dependencies are missing; install requirements.txt"
            ) from exc
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def read_leads(self) -> list[LeadRecord]:
        # Defense in depth: callers that use the reader directly must not be
        # able to contact Google while production synchronization is paused.
        if not getattr(self.settings, "google_sheets_sync_enabled", False):
            LOGGER.info("Google Sheets lead sync is disabled; skipping read")
            return []

        if not self.settings.google_spreadsheet_id:
            raise GoogleSheetsConfigError("GOOGLE_SPREADSHEET_ID is required")

        if self.settings.google_sheets_source == "apps_script":
            return self._read_from_apps_script()
        if self.settings.google_sheets_source != "api":
            raise GoogleSheetsConfigError(
                "GOOGLE_SHEETS_SOURCE must be either 'api' or 'apps_script'"
            )

        service = self._service()
        metadata = (
            service.spreadsheets()
            .get(
                spreadsheetId=self.settings.google_spreadsheet_id,
                fields="sheets(properties(sheetId,title))",
            )
            .execute()
        )
        target_title = None
        for sheet in metadata.get("sheets", []):
            properties = sheet.get("properties", {})
            if int(properties.get("sheetId", -1)) == self.settings.google_sheet_gid:
                target_title = properties.get("title")
                break
        if not target_title:
            raise GoogleSheetsConfigError(
                f"Sheet gid {self.settings.google_sheet_gid} was not found"
            )

        escaped_title = target_title.replace("'", "''")
        response = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=self.settings.google_spreadsheet_id,
                range=f"'{escaped_title}'!A:X",
                valueRenderOption="UNFORMATTED_VALUE",
            )
            .execute()
        )
        rows = response.get("values", [])
        if not rows:
            return []
        headers = [str(value).strip() for value in rows[0]]
        leads: list[LeadRecord] = []
        for values in rows[1:]:
            lead = LeadRecord.from_sheet_row(headers, list(values))
            if lead is not None:
                leads.append(lead)
        return leads

    def _read_from_apps_script(self) -> list[LeadRecord]:
        url = self.settings.google_apps_script_url
        token = self.settings.google_apps_script_token
        if not url or not token:
            raise GoogleSheetsConfigError(
                "GOOGLE_APPS_SCRIPT_URL and GOOGLE_APPS_SCRIPT_TOKEN are required "
                "when GOOGLE_SHEETS_SOURCE=apps_script"
            )
        try:
            response = httpx.post(
                url,
                json={"token": token, "gid": self.settings.google_sheet_gid},
                timeout=30,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GoogleSheetsConfigError("Apps Script lead endpoint request failed") from exc
        if payload.get("ok") is not True:
            raise GoogleSheetsConfigError(payload.get("error", "Apps Script endpoint rejected request"))

        headers = [str(value).strip() for value in payload.get("headers", [])]
        return [
            lead
            for values in payload.get("values", [])
            if (lead := LeadRecord.from_sheet_row(headers, list(values))) is not None
        ]


class LeadSyncService:
    def __init__(
        self,
        reader: GoogleSheetsReader,
        repository: LeadRepository,
        *,
        enabled: bool | None = None,
    ) -> None:
        self.reader = reader
        self.repository = repository
        # The explicit argument is useful for tests and alternate entrypoints;
        # normal callers inherit the setting from the reader.  A missing
        # setting is treated as disabled for safety.
        reader_setting = getattr(
            getattr(reader, "settings", None),
            "google_sheets_sync_enabled",
            None,
        )
        if enabled is not None:
            # A real reader's setting can only further restrict an explicit
            # override.  Lightweight test/dummy readers may not expose a
            # settings object, in which case the explicit value is honored.
            self.enabled = (
                bool(enabled)
                if reader_setting is None
                else bool(enabled) and bool(reader_setting)
            )
        else:
            self.enabled = bool(reader_setting)

    def sync_once(self) -> LeadSyncStats:
        if not self.enabled:
            LOGGER.info("Google Sheets lead sync is disabled; no rows synchronized")
            return LeadSyncStats(inserted=0, skipped=0)
        stats = self.repository.append(self.reader.read_leads())
        LOGGER.info("lead sync completed: inserted=%d skipped=%d", stats.inserted, stats.skipped)
        return stats

    def run_forever(self, interval_seconds: int = 60) -> None:
        if interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if not self.enabled:
            LOGGER.info("Google Sheets lead sync is disabled; worker will exit")
            return
        while True:
            try:
                self.sync_once()
            except Exception:
                LOGGER.exception("lead sync failed; retrying on the next interval")
            time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Google Sheets leads into MySQL")
    parser.add_argument("--once", action="store_true", help="perform one sync and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = AppSettings.from_env()
    if not settings.google_sheets_sync_enabled:
        LOGGER.info(
            "Google Sheets lead sync is disabled; exiting without creating clients"
        )
        return
    service = LeadSyncService(
        GoogleSheetsReader(settings),
        LeadRepository(settings),
        enabled=settings.google_sheets_sync_enabled,
    )
    try:
        if args.once:
            service.sync_once()
        else:
            service.run_forever(settings.google_sheets_poll_interval_seconds)
    except GoogleSheetsConfigError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
