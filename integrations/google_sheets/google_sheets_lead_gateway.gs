/**
 * Google 表格只读网关。
 *
 * Python 使用两个动作：
 * 1. tail：打开接收时取得表格当前末行，历史行全部跳过。
 * 2. read：接收开启期间，从指定行开始分批读取新行。
 *
 * 本脚本不主动推送、不修改表格，也不保存 Python 的读取进度。
 */

// 部署前替换为足够长的随机字符串，并在 Python 的 .env 中配置相同值。
const INITIAL_ACCESS_TOKEN = 'REPLACE_WITH_A_LONG_RANDOM_SECRET';

const HEADER_ROW = 1;
const DEFAULT_BATCH_SIZE = 100;
const MAX_BATCH_SIZE = 500;

/**
 * 首次安装时在 Apps Script 编辑器中手动运行一次。
 * 脚本必须绑定到目标 Google 表格。
 */
function setup() {
  if (INITIAL_ACCESS_TOKEN === 'REPLACE_WITH_A_LONG_RANDOM_SECRET') {
    throw new Error('请先替换 INITIAL_ACCESS_TOKEN。');
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (!spreadsheet) {
    throw new Error('请从目标 Google 表格的“扩展程序 > Apps Script”创建并运行此脚本。');
  }

  PropertiesService.getScriptProperties().setProperties({
    ACCESS_TOKEN: INITIAL_ACCESS_TOKEN,
    SPREADSHEET_ID: spreadsheet.getId(),
  });
}

function doPost(event) {
  try {
    const request = parseRequest(event);
    verifyToken(request.token);

    const sheet = findSheet(request.gid);
    const action = String(request.action || '').trim().toLowerCase();

    if (action === 'tail') {
      return jsonOutput(readTail(sheet));
    }
    if (action === 'read') {
      return jsonOutput(readRows(sheet, request.start_row, request.limit));
    }

    return jsonOutput({ok: false, error: 'unsupported_action'});
  } catch (error) {
    return jsonOutput({ok: false, error: String(error.message || error)});
  }
}

function parseRequest(event) {
  const body = event && event.postData ? event.postData.contents : '';
  if (!body) {
    throw new Error('empty_request_body');
  }

  try {
    const request = JSON.parse(body);
    if (!request || typeof request !== 'object' || Array.isArray(request)) {
      throw new Error('invalid_json');
    }
    return request;
  } catch (error) {
    throw new Error('invalid_json');
  }
}

function verifyToken(requestToken) {
  const configuredToken = PropertiesService.getScriptProperties().getProperty('ACCESS_TOKEN');
  if (!configuredToken) {
    throw new Error('gateway_not_configured');
  }
  if (!requestToken || String(requestToken) !== configuredToken) {
    throw new Error('unauthorized');
  }
}

function findSheet(requestedGid) {
  const spreadsheetId = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID');
  if (!spreadsheetId) {
    throw new Error('gateway_not_configured');
  }

  if (requestedGid === undefined || requestedGid === null || requestedGid === '') {
    throw new Error('invalid_gid');
  }
  const gid = Number(requestedGid);
  if (!Number.isInteger(gid) || gid < 0) {
    throw new Error('invalid_gid');
  }

  const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  const sheet = spreadsheet.getSheets().find(item => item.getSheetId() === gid);
  if (!sheet) {
    throw new Error('sheet_gid_not_found');
  }
  return sheet;
}

function readTail(sheet) {
  const lastRow = Math.max(sheet.getLastRow(), HEADER_ROW);
  return {
    ok: true,
    action: 'tail',
    gid: sheet.getSheetId(),
    sheet_name: sheet.getName(),
    last_row: lastRow,
    next_row: lastRow + 1,
  };
}

function readRows(sheet, requestedStartRow, requestedLimit) {
  const startRow = Number(requestedStartRow);
  if (!Number.isInteger(startRow) || startRow <= HEADER_ROW) {
    throw new Error('invalid_start_row');
  }

  const limit = normalizeLimit(requestedLimit);
  const lastRow = Math.max(sheet.getLastRow(), HEADER_ROW);
  const lastColumn = sheet.getLastColumn();
  if (lastColumn < 1) {
    throw new Error('header_not_found');
  }

  const headers = sheet.getRange(HEADER_ROW, 1, 1, lastColumn).getValues()[0];
  if (startRow > lastRow) {
    return {
      ok: true,
      action: 'read',
      gid: sheet.getSheetId(),
      headers: headers,
      start_row: startRow,
      next_row: startRow,
      last_row: lastRow,
      has_more: false,
      values: [],
    };
  }

  const rowCount = Math.min(limit, lastRow - startRow + 1);
  const values = sheet.getRange(startRow, 1, rowCount, lastColumn).getValues();
  const nextRow = startRow + rowCount;

  return {
    ok: true,
    action: 'read',
    gid: sheet.getSheetId(),
    headers: headers,
    start_row: startRow,
    next_row: nextRow,
    last_row: lastRow,
    has_more: nextRow <= lastRow,
    values: values,
  };
}

function normalizeLimit(requestedLimit) {
  if (requestedLimit === undefined || requestedLimit === null || requestedLimit === '') {
    return DEFAULT_BATCH_SIZE;
  }

  const limit = Number(requestedLimit);
  if (!Number.isInteger(limit) || limit < 1) {
    throw new Error('invalid_limit');
  }
  return Math.min(limit, MAX_BATCH_SIZE);
}

function jsonOutput(value) {
  return ContentService
    .createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
