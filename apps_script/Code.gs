/**
 * uzlat-bot → Google Sheet logger (Apps Script web app).
 * Deploy: Deploy → New deployment → Web app; Execute as: Me; Who has access: Anyone.
 * The sheet itself stays private; only this script (running as you) writes to it.
 */
const SECRET = 'CHANGE_ME';   // same value as SHEETS_WEBHOOK_SECRET on Vercel
const SHEET_NAME = 'Startlar';
const HEADERS = ['Vaqt (Toshkent)', 'Telegram ID', 'Username', 'Ism', 'Familiya',
                 'Til', 'Premium', 'Manba', 'Holat'];

function doPost(e) {
  let data;
  try { data = JSON.parse(e.postData.contents); } catch (err) { return json_({ ok: false }); }
  if (data.secret !== SECRET) return json_({ ok: false });

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let sh = ss.getSheetByName(SHEET_NAME);
    if (!sh) sh = ss.insertSheet(SHEET_NAME, 0);
    if (sh.getLastRow() === 0) {
      sh.appendRow(HEADERS);
      sh.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold').setBackground('#1f2a44').setFontColor('#ffffff');
      sh.setFrozenRows(1);
    }
    const last = sh.getLastRow();
    const ids = last > 1 ? sh.getRange(2, 2, last - 1, 1).getValues().map(r => String(r[0])) : [];
    const isNew = ids.indexOf(String(data.user_id)) === -1;
    const when = Utilities.formatDate(new Date((data.ts || Date.now() / 1000) * 1000),
                                      'Asia/Tashkent', 'yyyy-MM-dd HH:mm:ss');
    sh.appendRow([when, String(data.user_id), data.username || '', data.first_name || '',
                  data.last_name || '', data.language || '', data.premium ? 'Ha' : '',
                  data.source || '', isNew ? 'Yangi' : 'Qayta']);
    return json_({ ok: true, new: isNew });
  } finally {
    lock.releaseLock();
  }
}

function doGet() { return json_({ ok: true, service: 'uzlat-bot logger' }); }

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
