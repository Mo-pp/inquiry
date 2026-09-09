/**
 * Minimal read-only gateway for the local lead_sync worker.
 *
 * Bind this script to the Google Sheet (Extensions > Apps Script), replace
 * ACCESS_TOKEN below with a long random value, run setup once, then deploy as
 * a Web app that executes as you. The local worker sends the token in a POST
 * body, so the sheet is never published publicly.
 */
const ACCESS_TOKEN = 'REPLACE_WITH_A_LONG_RANDOM_SECRET';
// Optional remote kill switch.  Leave the deployed version unchanged when
// the form should keep writing to Google Sheets; the local Python gate is the
// normal pause point for this project.
const PRODUCTION_SYNC_ENABLED = false;

function setup() {
  if (ACCESS_TOKEN === 'REPLACE_WITH_A_LONG_RANDOM_SECRET') {
    throw new Error('Replace ACCESS_TOKEN before running setup().');
  }
  const spreadsheetId = SpreadsheetApp.getActiveSpreadsheet().getId();
  PropertiesService.getScriptProperties().setProperties({
    ACCESS_TOKEN: ACCESS_TOKEN,
    SPREADSHEET_ID: spreadsheetId,
  });
}

function doPost(event) {
  try {
    if (!PRODUCTION_SYNC_ENABLED) {
      return output({ok: false, disabled: true, error: 'production_sync_disabled'});
    }
    const request = JSON.parse((event.postData && event.postData.contents) || '{}');
    const properties = PropertiesService.getScriptProperties();
    if (request.token !== properties.getProperty('ACCESS_TOKEN')) {
      return output({ok: false, error: 'unauthorized'});
    }

    const spreadsheet = SpreadsheetApp.openById(
      properties.getProperty('SPREADSHEET_ID')
    );
    const gid = Number(request.gid || 0);
    const sheet = spreadsheet.getSheets().find(item => item.getSheetId() === gid);
    if (!sheet) {
      return output({ok: false, error: 'sheet gid not found'});
    }

    const rows = sheet.getDataRange().getValues();
    const headers = rows.length ? rows[0] : [];
    return output({ok: true, headers: headers, values: rows.slice(1)});
  } catch (error) {
    return output({ok: false, error: String(error)});
  }
}

function output(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
