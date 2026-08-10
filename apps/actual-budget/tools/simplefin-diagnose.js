// Read-only SimpleFIN bank-sync diagnostic for the Actual Budget server.
//
// Answers "why is bank sync not pulling?" without guessing, by comparing what the
// SimpleFIN bridge is actually serving against what the budget file is linked to.
// It never prints the access key and never writes anything.
//
// Run:
//   kubectl --context home-k3s -n actual-budget exec -i deploy/actual-budget -- \
//     sh -c 'cat > /tmp/d.js && node /tmp/d.js; rm -f /tmp/d.js /tmp/budget.sqlite' \
//     < apps/actual-budget/tools/simplefin-diagnose.js
//
// Reading of the three outcomes:
//   HTTP != 200          -> the access key really is dead; do the reconnect in
//                           docs/finance/simplefin-reconnect.md.
//   MATCH but stale      -> the link is fine and data is waiting; the failure is
//                           client-side (sync not triggered, or an Actual version
//                           that aborts the batch, see the runbook).
//   ORPHAN               -> the bridge no longer serves that account id. Re-authorize
//                           that bank at the bridge, then RE-LINK in Actual: the new
//                           account id will not match the old one.

const fs = require('fs');
const zlib = require('zlib');
const Database = require('/app/node_modules/better-sqlite3');

const SERVER_DB = '/data/server-files/account.sqlite';
const USER_FILES = '/data/user-files';

function accessKey() {
  const db = new Database(SERVER_DB, { readonly: true });
  const row = db.prepare('select value from secrets where name = ?').get('simplefin_accessKey');
  if (!row) return null;
  return Buffer.isBuffer(row.value) ? row.value.toString('utf8') : row.value;
}

// The budget blob is a zip holding db.sqlite. Extract it to /tmp so we can read the
// account linkage, which lives in the budget file and not in the server db.
function extractBudget() {
  const blob = fs.readdirSync(USER_FILES).find((f) => f.endsWith('.blob'));
  if (!blob) return null;
  const buf = fs.readFileSync(`${USER_FILES}/${blob}`);
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0; i--) {
    if (buf.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) return null;
  const count = buf.readUInt16LE(eocd + 10);
  let off = buf.readUInt32LE(eocd + 16);
  for (let k = 0; k < count; k++) {
    const method = buf.readUInt16LE(off + 10);
    const csize = buf.readUInt32LE(off + 20);
    const nlen = buf.readUInt16LE(off + 28);
    const elen = buf.readUInt16LE(off + 30);
    const clen = buf.readUInt16LE(off + 32);
    const lho = buf.readUInt32LE(off + 42);
    const name = buf.slice(off + 46, off + 46 + nlen).toString();
    if (name.endsWith('db.sqlite')) {
      const dstart = lho + 30 + buf.readUInt16LE(lho + 26) + buf.readUInt16LE(lho + 28);
      const raw = buf.slice(dstart, dstart + csize);
      fs.writeFileSync('/tmp/budget.sqlite', method === 0 ? raw : zlib.inflateRawSync(raw));
      return '/tmp/budget.sqlite';
    }
    off += 46 + nlen + elen + clen;
  }
  return null;
}

(async () => {
  const key = accessKey();
  if (!key) return console.log('NO ACCESS KEY: bank sync was never set up on this server.');

  const u = new URL(key);
  const auth = 'Basic ' + Buffer.from(
    decodeURIComponent(u.username) + ':' + decodeURIComponent(u.password),
  ).toString('base64');
  const base = u.origin + u.pathname.replace(/\/$/, '');
  console.log(`bridge: ${u.host}${u.pathname}`);

  // 44 days: the bridge warns above its recommended 45-day window.
  const start = Math.floor((Date.now() - 44 * 24 * 3600 * 1000) / 1000);
  let res;
  try {
    res = await fetch(`${base}/accounts?start-date=${start}`, { headers: { Authorization: auth } });
  } catch (e) {
    return console.log('FETCH FAILED:', e.message.replace(/https?:\/\/\S+/g, '<redacted>'));
  }
  console.log(`bridge HTTP ${res.status} ${res.statusText}`);
  if (res.status !== 200) {
    return console.log('body:', (await res.text()).slice(0, 400));
  }

  const data = await res.json();
  const serving = (data.accounts || []).map((a) => ({
    id: a.id,
    label: `${(a.org && (a.org.name || a.org.domain)) || '?'} / ${a.name}`,
    txns: (a.transactions || []).length,
  }));
  if ((data.errors || []).length) console.log('bridge errors:', JSON.stringify(data.errors));

  console.log(`\nSERVING (${serving.length}):`);
  for (const a of serving) console.log(`  ${a.label} -- ${a.txns} txns in 44d -- ${a.id}`);

  const budgetPath = extractBudget();
  if (!budgetPath) return console.log('\nNo budget blob found; cannot check linkage.');
  const bud = new Database(budgetPath, { readonly: true });
  const linked = bud
    .prepare("select name, account_id, last_sync from accounts where tombstone = 0 and account_sync_source = 'simpleFin'")
    .all();

  const servingIds = new Set(serving.map((a) => a.id));
  console.log(`\nLINKED (${linked.length}):`);
  for (const l of linked) {
    const when = l.last_sync ? new Date(Number(l.last_sync)).toISOString().slice(0, 10) : 'NEVER';
    console.log(`  ${servingIds.has(l.account_id) ? 'MATCH ' : 'ORPHAN'}  ${l.name.padEnd(30)} last_sync=${when}`);
  }

  const linkedIds = new Set(linked.map((l) => l.account_id));
  const unclaimed = serving.filter((a) => !linkedIds.has(a.id));
  if (unclaimed.length) {
    console.log('\nSERVED BUT NOT LINKED (link these in Actual to start importing):');
    for (const a of unclaimed) console.log(`  ${a.label} -- ${a.id}`);
  }
})();
