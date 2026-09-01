/**
 * Browser E2E: đơn vị từ chối → khách bấm tìm đơn vị khác → đơn vị B duyệt.
 *
 * Chạy (cùng cách với `provider_proposal_journey.mjs`):
 *   backend trên p118_e2e_db, cờ bật, cổng 8100
 *   vite dev --port 5290 với VITE_API_PROXY_TARGET trỏ về backend ấy
 *   node provider_reselection_journey.mjs
 *
 * KHÔNG gọi model. Dữ liệu gieo bằng SQL tất định; đường qua model đã có canary
 * riêng. Thứ đang được kiểm là giao diện và ba endpoint thật nó gọi.
 *
 * Database: CHỈ `p118_e2e_db`.
 */

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'

const PW = 'Passw0rd!123'
const APP = process.env.P118_APP ?? 'http://127.0.0.1:5290'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'

const sql = (q) =>
  execFileSync('psql', ['-d', DB, '-tAc', q], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

const duoc = sql('SELECT current_database()')[0]
if (duoc !== DB) {
  console.log(`DỪNG: đang kết nối ${duoc}, không phải ${DB}`)
  process.exit(2)
}
console.log(`database: ${duoc}`)

const loi = []
const check = (ten, dat, ct = '') => {
  console.log(`  ${dat ? '✓' : '✗'} ${ten}${ct ? ` — ${ct}` : ''}`)
  if (!dat) loi.push(ten)
}
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}

const LY_DO = 'Đội xe bên mình đang bảo trì toàn bộ, xin phép từ chối.'
const GIA = { 'MOV-01': 430000, 'MOV-02': 470000, 'MOV-03': 420000 }

/** Gieo một workflow đã được khách đồng ý và vừa bị đơn vị `ma` TỪ CHỐI. */
function gieoDaBiTuChoi(uid, ma) {
  const wid = randomUUID()
  const quote = randomUUID()
  const proposal = randomUUID()
  const input = JSON.stringify({
    move_date: '2026-09-30',
    move_time: '08:00',
    move_vehicle: 'van',
    needs_elevator: false,
    needs_loading_support: false,
  })
  sql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
       VALUES (${q(wid)}::uuid,'Đặt lịch chuyển nhà','WAITING_APPROVAL',${q(uid)}::uuid)`)
  sql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)
       VALUES (${q(wid)}::uuid,'T1','schedule_move','WAITING_APPROVAL','[]'::jsonb,${q(input)}::jsonb)`)
  sql(`INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount,
                                   currency, request_fingerprint, valid_until, workflow_id, task_id, status, confirmed_at)
       VALUES (${q(quote)}::uuid, ${q('Q-' + quote.slice(0, 8))}, ${q(ma)}, 'schedule_move', ${GIA[ma]},
               'VND', ${q('vt' + wid.slice(0, 8))}, NOW() + INTERVAL '45 min', ${q(wid)}::uuid, 'T1',
               'CONFIRMED', NOW())`)
  sql(`INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status, confirmed_at)
       VALUES (${q(proposal)}::uuid, ${q(wid)}::uuid,'T1',${q(quote)}::uuid,'CONFIRMED', NOW())`)
  sql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status,
                                      service_provider_id, reject_code, reject_reason, decided_by, decided_at)
       VALUES (${q(wid)}::uuid,'T1','schedule_move','Chuyển nhà','{}'::jsonb,'REJECTED',
               ${q(ma)},'SERVICE_UNAVAILABLE',${q(LY_DO)},'dv_e2e', NOW())`)
  return { wid, proposal }
}

const dem = (wid, bang, dk = '') =>
  Number(sql(`SELECT count(*) FROM ${bang} WHERE workflow_id=${q(wid)}::uuid ${dk}`)[0])

const b = await chromium.launch()
const page = await (await b.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
const jsErr = []
page.on('pageerror', (e) => jsErr.push(String(e)))

const U = 'rj' + Math.floor(Math.random() * 1e6)
await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
const uid = sql(`SELECT id FROM users WHERE username=${q(U)}`)[0]
await page.goto(`${APP}/login`)
await page.fill('#login-username', U)
await page.fill('#login-password', PW)
await page.click('button[type=submit]')
await page.waitForURL('**/workspace', { timeout: 30000 })

const theTuChoi = () => page.locator('[data-testid="provider-rejection"]')
const theDeXuat = () => page.locator('[data-testid="provider-proposal"]')
const mo = async (wid) => {
  await page.goto(`${APP}/workspace?w=${wid}`)
  await page.waitForTimeout(2500)
}

console.log('\n1 — khách thấy lời từ chối và LÝ DO thật, không thấy "đang chờ đơn vị"')
const { wid } = gieoDaBiTuChoi(uid, 'MOV-03')
await mo(wid)
{
  const body = (await page.textContent('body')).replace(/\s+/g, ' ')
  check('thấy thẻ từ chối', (await theTuChoi().count()) === 1)
  check('hiện tên đơn vị', body.includes('Dịch vụ An Khang'))
  check('hiện lý do thật', body.includes('Đội xe bên mình đang bảo trì toàn bộ'))
  check('KHÔNG nói "đang chờ đơn vị"', !/đang chờ đơn vị/i.test(body))
  check('không hiện mã kỹ thuật', !body.includes('MOV-03'))
  check('có nút tìm đơn vị khác', (await page.locator('[data-testid="rejection-find-another"]').count()) === 1)
  check('chưa mở lần thử mới', dem(wid, 'workflow_tasks') === 1)
}

console.log('\n2 — F5 trước khi bấm: thẻ vẫn còn')
await page.reload()
await page.waitForTimeout(2500)
check('F5 giữ nguyên thẻ từ chối', (await theTuChoi().count()) === 1)
check('F5 không mở lần thử mới', dem(wid, 'workflow_tasks') === 1)

console.log('\n3 — bấm "tìm đơn vị khác" → đề xuất đơn vị KHÁC')
await page.locator('[data-testid="rejection-find-another"]').click()
await page.waitForTimeout(4000)
{
  check('T1 thành CANCELLED, T1R2 ra đời', dem(wid, 'workflow_tasks') === 2)
  const buoc = sql(`SELECT task_id || '=' || status FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`)
  check('vai của hai lần thử đúng', buoc.join(' ') === 'T1=CANCELLED T1R2=WAITING_APPROVAL', buoc.join(' '))
  const cu = sql(`SELECT status || '|' || coalesce(reject_reason,'-') FROM service_approvals WHERE workflow_id=${q(wid)}::uuid AND task_id='T1'`)[0]
  check('bằng chứng cũ nguyên vẹn', cu === `REJECTED|${LY_DO}`, cu)
  check('chưa mở hàng đợi cho đơn vị mới', dem(wid, 'service_approvals', "AND task_id='T1R2'") === 0)
  check('không còn thẻ từ chối', (await theTuChoi().count()) === 0)
  check('hiện thẻ đề xuất mới', (await theDeXuat().count()) === 1)
  const ma = sql(`SELECT q.service_provider_id FROM service_quotes q
                  JOIN service_provider_proposals p ON p.quote_id=q.quote_id
                  WHERE p.workflow_id=${q(wid)}::uuid AND p.task_id='T1R2'`)[0]
  check('đơn vị mới KHÁC đơn vị đã từ chối', ma !== 'MOV-03', ma)
}

console.log('\n4 — khách đồng ý với đơn vị mới')
await page.locator('[data-testid="proposal-confirm"]').click()
await page.waitForTimeout(4000)
{
  const dong = sql(`SELECT task_id || '=' || status || '/' || service_provider_id
                    FROM service_approvals WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`)
  check('hai dòng duyệt, hai vai khác nhau', dong.length === 2 && dong[0].startsWith('T1=REJECTED') && dong[1].includes('T1R2=AWAITING'), dong.join(' '))
  check('không còn nút hành động nào', (await page.locator('[data-testid="proposal-confirm"], [data-testid="rejection-find-another"]').count()) === 0)
}

console.log('\n5 — đơn vị mới duyệt → workflow chạy tiếp')
{
  const ma = sql(`SELECT service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid AND task_id='T1R2'`)[0]
  const dvU = 'dvb' + Math.floor(Math.random() * 1e6)
  await api('/auth/register', { method: 'POST', body: { username: dvU, password: PW } })
  sql(`UPDATE users SET role='provider' WHERE username=${q(dvU)}`)
  sql(`INSERT INTO service_provider_accounts (user_id, service_provider_id)
       SELECT id, ${q(ma)} FROM users WHERE username=${q(dvU)} ON CONFLICT DO NOTHING`)
  const tok = (await api('/auth/login', { method: 'POST', body: { username: dvU, password: PW } })).json.access_token
  const r = await api(`/service-approvals/${wid}/T1R2/decide`, { token: tok, method: 'POST', body: { decision: 'approve' } })
  check('đơn vị mới duyệt được', r.status === 200, `http ${r.status}`)
  await new Promise((rs) => setTimeout(rs, 6000))
  const st = sql(`SELECT status FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid AND task_id='T1R2'`)[0]
  check('bước mới chạy xong', st === 'SUCCESS', st)
  check('bước cũ vẫn CANCELLED', sql(`SELECT status FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid AND task_id='T1'`)[0] === 'CANCELLED')
}

console.log('\n6 — F5 sau khi hoàn tất: không mở lại nút nào')
await page.reload()
await page.waitForTimeout(3000)
check('không thẻ từ chối', (await theTuChoi().count()) === 0)
check('không thẻ đề xuất', (await theDeXuat().count()) === 0)
check('không sinh lần thử thứ ba', dem(wid, 'workflow_tasks') === 2)

console.log('\n7 — bấm đúp không tạo hai lần thử')
{
  const { wid: w2 } = gieoDaBiTuChoi(uid, 'MOV-02')
  await mo(w2)
  const nut = page.locator('[data-testid="rejection-find-another"]')
  await Promise.all([nut.click(), nut.click().catch(() => {})])
  await page.waitForTimeout(4500)
  check('chỉ một lần thử mới', dem(w2, 'workflow_tasks') === 2, `${dem(w2, 'workflow_tasks')} bước`)
  check('chỉ một đề xuất đang chờ', dem(w2, 'service_provider_proposals', "AND status='PROPOSED'") === 1)
}

console.log('\nlỗi JS trên trang:', jsErr.length ? jsErr.join(' | ') : 'không')
console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
