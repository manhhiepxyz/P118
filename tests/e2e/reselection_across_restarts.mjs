/**
 * Restart TIẾN TRÌNH THẬT ở ba khe của luồng chọn lại đơn vị.
 *
 * Khác các bài "đọc nguội" (`_DEMO_JOBS.clear()`): ở đó chỉ cache RAM bị xoá,
 * còn tiến trình vẫn sống — mọi thứ khởi tạo lúc startup vẫn nguyên. Bài này
 * GIẾT tiến trình backend và khởi động lại, nên nó là bài duy nhất trả lời
 * được câu "một tiến trình thứ hai đọc lại dữ liệu này thì thấy gì".
 *
 * Hai loại bài đều có giá trị và không thay thế nhau. Gọi đọc nguội là
 * "process-restart test" là nói quá về thứ nó đo.
 *
 * BỐN PHA, harness lo việc dừng/khởi động backend:
 *
 *   node reselection_across_restarts.mjs seed     → gieo, khách thấy lời từ chối
 *   … restart …  verify1                          → vẫn lời từ chối, bấm tìm đơn vị khác
 *   … restart …  verify2                          → vẫn đề xuất B, confirm
 *   … restart …  verify3                          → vẫn chờ đơn vị B, B duyệt
 *
 * PostgreSQL và volume KHÔNG bị đụng tới; chỉ backend restart.
 * Database: CHỈ `p118_e2e_db`.
 */

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'

const PHA = process.argv[2] ?? 'seed'
const PW = 'Passw0rd!123'
const APP = process.env.P118_APP ?? 'http://127.0.0.1:5290'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const MOC = '/tmp/p118_reselection_restart.json'

const sql = (q) =>
  execFileSync('psql', ['-d', DB, '-tAc', q], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

if (sql('SELECT current_database()')[0] !== DB) {
  console.log('DỪNG: sai database')
  process.exit(2)
}

const loi = []
const check = (ten, dat, ct = '') => {
  console.log(`  ${dat ? '✓' : '✗'} ${ten}${ct ? ` — ${ct}` : ''}`)
  if (!dat) loi.push(`${PHA}:${ten}`)
}
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}

/* Backend sống lại VÀ đang đọc đúng kho nào.
   `/ready` xanh chỉ nói "tiến trình lên"; nó không nói tiến trình ấy nối vào
   database nào. Bằng chứng thật là một dòng chỉ có trong `p118_e2e_db`: ở pha
   seed là một hàng vừa ghi bằng psql, ở các pha sau là chính workflow canary.
   Nếu backend nối nhầm kho, dòng ấy không tồn tại và bài dừng ngay tại đây. */
async function readySachSe(widMoc) {
  const r = await fetch(`${API}/ready`)
  const j = await r.json().catch(() => ({}))
  if (r.status !== 200 || j.status !== 'ready') return `/ready ${r.status} ${j.status ?? ''}`
  const t = JSON.stringify(j)
  if (/postgresql:\/\/|password|api[_-]?key/i.test(t)) return '/ready rò rỉ cấu hình'
  if (!widMoc) return null
  const tok = (await api('/auth/login', { method: 'POST', body: { username: widMoc.user, password: PW } })).json?.access_token
  if (!tok) return 'không đăng nhập được sau restart'
  const w = await api('/workflows/demo', { token: tok })
  if (w.status !== 200) return `không đọc được danh sách (http ${w.status})`
  const co = JSON.stringify(w.json ?? {}).includes(widMoc.wid)
  return co ? null : 'backend không thấy workflow canary — nhiều khả năng nối nhầm database'
}

const LY_DO = 'Đội xe bên mình đang bảo trì toàn bộ, xin phép từ chối.'
const GIA = { 'MOV-01': 430000, 'MOV-02': 470000, 'MOV-03': 420000 }

const b = await chromium.launch()
const page = await (await b.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
const theTuChoi = () => page.locator('[data-testid="provider-rejection"]')
const theDeXuat = () => page.locator('[data-testid="provider-proposal"]')

async function dangNhap(user) {
  await page.goto(`${APP}/login`)
  await page.fill('#login-username', user)
  await page.fill('#login-password', PW)
  await page.click('button[type=submit]')
  await page.waitForURL('**/workspace', { timeout: 30000 })
}
const mo = async (wid) => {
  await page.goto(`${APP}/workspace?w=${wid}`)
  await page.waitForTimeout(2500)
}
const doc = () => JSON.parse(readFileSync(MOC, 'utf8'))
const ghi = (d) => writeFileSync(MOC, JSON.stringify(d))
const dem = (wid, bang, dk = '') =>
  Number(sql(`SELECT count(*) FROM ${bang} WHERE workflow_id=${q(wid)}::uuid ${dk}`)[0])



if (PHA === 'seed') {
  check('backend sẵn sàng', (await readySachSe(null)) === null)
  const U = 'rr' + Math.floor(Math.random() * 1e6)
  await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
  const uid = sql(`SELECT id FROM users WHERE username=${q(U)}`)[0]
  const wid = randomUUID()
  const quote = randomUUID()
  const proposal = randomUUID()
  const ma = 'MOV-03'
  const input = JSON.stringify({
    move_date: '2026-09-30', move_time: '08:00', move_vehicle: 'van',
    needs_elevator: false, needs_loading_support: false,
  })
  sql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
       VALUES (${q(wid)}::uuid,'Đặt lịch chuyển nhà','WAITING_APPROVAL',${q(uid)}::uuid)`)
  sql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)
       VALUES (${q(wid)}::uuid,'T1','schedule_move','WAITING_APPROVAL','[]'::jsonb,${q(input)}::jsonb)`)
  sql(`INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount,
                                   currency, request_fingerprint, valid_until, workflow_id, task_id, status, confirmed_at)
       VALUES (${q(quote)}::uuid, ${q('Q-' + quote.slice(0, 8))}, ${q(ma)}, 'schedule_move', ${GIA[ma]},
               'VND', ${q('vt' + wid.slice(0, 8))}, NOW() + INTERVAL '90 min', ${q(wid)}::uuid, 'T1','CONFIRMED', NOW())`)
  sql(`INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status, confirmed_at)
       VALUES (${q(proposal)}::uuid, ${q(wid)}::uuid,'T1',${q(quote)}::uuid,'CONFIRMED', NOW())`)
  sql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,
                                      service_provider_id, reject_code, reject_reason, decided_by, decided_at)
       VALUES (${q(wid)}::uuid,'T1','schedule_move','Chuyển nhà','{}'::jsonb,'REJECTED',
               ${q(ma)},'SERVICE_UNAVAILABLE',${q(LY_DO)},'dv_e2e', NOW())`)

  await dangNhap(U)
  await mo(wid)
  console.log('Pha SEED — khách thấy lời từ chối')
  check('thấy thẻ từ chối', (await theTuChoi().count()) === 1)
  check('chưa có lần thử mới', dem(wid, 'workflow_tasks') === 1)
  ghi({ user: U, wid, quote, proposal, ma })
} else if (PHA === 'verify1') {
  const d = doc()
  const vd = await readySachSe(d)
  check('backend restart xong và đọc đúng p118_e2e_db', vd === null, vd ?? '')
  await dangNhap(d.user)
  await mo(d.wid)
  console.log('Khe 1 — restart SAU reject, TRƯỚC khi khách bấm')
  check('thẻ từ chối vẫn còn', (await theTuChoi().count()) === 1)
  check('vẫn đúng bước cũ', (await theTuChoi().first().getAttribute('data-task-id')) === 'T1')
  check('lý do vẫn nguyên', (await page.textContent('body')).includes('đang bảo trì toàn bộ'))
  check('chưa sinh lần thử mới', dem(d.wid, 'workflow_tasks') === 1)
  check('chứng từ cũ không đổi', sql(`SELECT status FROM service_quotes WHERE quote_id=${q(d.quote)}::uuid`)[0] === 'CONFIRMED')

  await page.locator('[data-testid="rejection-find-another"]').click()
  await page.waitForTimeout(4500)
  check('bấm sau restart vẫn mở được lần thử mới', dem(d.wid, 'workflow_tasks') === 2)
  const moi = sql(`SELECT p.proposal_id || '|' || q.service_provider_id
                   FROM service_provider_proposals p JOIN service_quotes q ON q.quote_id=p.quote_id
                   WHERE p.workflow_id=${q(d.wid)}::uuid AND p.task_id='T1R2'`)[0]
  const [pid, maMoi] = moi.split('|')
  check('đơn vị mới khác đơn vị đã từ chối', maMoi !== d.ma, maMoi)
  ghi({ ...d, proposalB: pid, maB: maMoi })
} else if (PHA === 'verify2') {
  const d = doc()
  const vd = await readySachSe(d)
  check('backend restart xong và đọc đúng p118_e2e_db', vd === null, vd ?? '')
  await dangNhap(d.user)
  await mo(d.wid)
  console.log('Khe 2 — restart SAU khi bấm, TRƯỚC khi đồng ý đề xuất B')
  check('thẻ đề xuất vẫn còn', (await theDeXuat().count()) === 1)
  check('vẫn đúng proposal B', (await theDeXuat().first().getAttribute('data-proposal-id')) === d.proposalB)
  check('không thẻ từ chối nào', (await theTuChoi().count()) === 0)
  check('không sinh lần thử thứ ba', dem(d.wid, 'workflow_tasks') === 2)
  check('chưa mở hàng đợi cho B', dem(d.wid, 'service_approvals', "AND task_id='T1R2'") === 0)

  await page.locator('[data-testid="proposal-confirm"]').click()
  await page.waitForTimeout(4000)
  check('confirm sau restart vẫn ăn', dem(d.wid, 'service_approvals', "AND task_id='T1R2'") === 1)
  ghi(d)
} else {
  const d = doc()
  const vd = await readySachSe(d)
  check('backend restart xong và đọc đúng p118_e2e_db', vd === null, vd ?? '')
  await dangNhap(d.user)
  await mo(d.wid)
  console.log('Khe 3 — restart SAU confirm B, TRƯỚC khi B quyết định')
  check('không còn nút hành động', (await page.locator('[data-testid="proposal-confirm"], [data-testid="rejection-find-another"]').count()) === 0)
  const dong = sql(`SELECT task_id || '=' || status || '/' || service_provider_id
                    FROM service_approvals WHERE workflow_id=${q(d.wid)}::uuid ORDER BY task_id`)
  check('hai dòng duyệt, hai vai', dong.join(' ') === `T1=REJECTED/${d.ma} T1R2=AWAITING/${d.maB}`, dong.join(' '))
  check('không nhân đôi đề xuất', dem(d.wid, 'service_provider_proposals') === 2)

  const dvU = 'dvr' + Math.floor(Math.random() * 1e6)
  await api('/auth/register', { method: 'POST', body: { username: dvU, password: PW } })
  sql(`UPDATE users SET role='provider' WHERE username=${q(dvU)}`)
  sql(`INSERT INTO service_provider_accounts (user_id, service_provider_id)
       SELECT id, ${q(d.maB)} FROM users WHERE username=${q(dvU)} ON CONFLICT DO NOTHING`)
  const tok = (await api('/auth/login', { method: 'POST', body: { username: dvU, password: PW } })).json.access_token
  const r = await api(`/service-approvals/${d.wid}/T1R2/decide`, { token: tok, method: 'POST', body: { decision: 'approve' } })
  check('đơn vị B duyệt được sau restart', r.status === 200, `http ${r.status}`)
  await new Promise((rs) => setTimeout(rs, 6000))
  check('bước mới chạy xong', sql(`SELECT status FROM workflow_tasks WHERE workflow_id=${q(d.wid)}::uuid AND task_id='T1R2'`)[0] === 'SUCCESS')
  check('bước cũ vẫn CANCELLED', sql(`SELECT status FROM workflow_tasks WHERE workflow_id=${q(d.wid)}::uuid AND task_id='T1'`)[0] === 'CANCELLED')
  console.log('\nIDs canary:')
  console.log('  workflow  :', d.wid)
  console.log('  T1        : CANCELLED · quote', d.quote.slice(0, 8), '· proposal', d.proposal.slice(0, 8), '· approval REJECTED/' + d.ma)
  console.log('  T1R2      : SUCCESS   · proposal', d.proposalB.slice(0, 8), '· approval APPROVED/' + d.maB)
}

console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
