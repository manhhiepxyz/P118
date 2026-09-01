/**
 * Browser E2E: card "Cần bạn xác nhận" hiện ĐÚNG loại việc, cho từng dịch vụ.
 *
 * Triệu chứng đã sửa: trang chi tiết suy loại việc bằng
 * `status === 'WAITING_APPROVAL' && !viewing_approval` rồi vẽ thẻ thanh toán —
 * nên một yêu cầu chuyển nhà đang chờ chọn đơn vị hiện ra tiêu đề "—", câu
 * "Chỗ đỗ xe đã được giữ…", và một nút chung.
 *
 * Bài này đo trên DOM THẬT vì đó là nơi triệu chứng sống. Bộ kiểm PostgreSQL đã
 * ghim hợp đồng; ở đây đo rằng giao diện đọc đúng hợp đồng ấy.
 *
 * Chạy: backend p118_e2e_db cổng 8100, vite 5290, cờ SERVICE_PROVIDER_MATCHING=1.
 * Bài này CHỈ ĐỌC trang chi tiết — không bấm nút quyết định nào.
 * Dọn dữ liệu canary sau khi chụp bằng chứng.
 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const APP = process.env.P118_APP ?? 'http://127.0.0.1:5290'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const PW = 'Passw0rd!123'
const HAU_TO = 'card' + Math.floor(Math.random() * 1e6)

const psql = (s) => execFileSync('psql', ['-d', DB, '-tAc', s], { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`
if (psql('SELECT current_database()') !== DB) { console.log('DỪNG: sai database'); process.exit(2) }

const loi = []
const check = (t, ok, ct = '') => { console.log(`    ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, { method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined })
  return { status: r.status, json: await r.json().catch(() => null) }
}
const DON_VI = JSON.parse(execFileSync('.venv/bin/python', ['-c',
  'import json,sys; sys.path.insert(0,"."); from src.orchestration.provider_directory import DON_VI_MAC_DINH; print(json.dumps(DON_VI_MAC_DINH))'],
  { encoding: 'utf8', cwd: '/Users/thanhtin/P-118' }))

const U = `kh_${HAU_TO}`
await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
const uid = psql(`SELECT id FROM users WHERE username=${q(U)}`)
const wids = []

function gieoWorkflow(tool, ct, trangThai = 'WAITING_APPROVAL') {
  const wid = crypto.randomUUID()
  wids.push(wid)
  const kh = JSON.stringify({ goal: tool, tasks: [{ task_id: 'T1', tool, depends_on: [], input: ct }] })
  psql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id, task_plan) VALUES (${q(wid)}::uuid,${q(tool)},${q(trangThai)},${q(uid)}::uuid,${q(kh)}::jsonb)`)
  psql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) VALUES (${q(wid)}::uuid,'T1',${q(tool)},'WAITING_APPROVAL','[]'::jsonb,${q(JSON.stringify(ct))}::jsonb)`)
  return wid
}

// ── 1. chuyển nhà đang chờ KHÁCH chọn đơn vị
const MOV = { move_date: '2026-12-01', move_time: '08:00', move_vehicle: 'van', needs_elevator: false, needs_loading_support: false }
const wDeXuat = gieoWorkflow('schedule_move', MOV)
const quoteId = crypto.randomUUID()
psql(`INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount, currency, request_fingerprint, valid_until, workflow_id, task_id, status)
      VALUES (${q(quoteId)}::uuid, ${q('Q-' + quoteId.slice(0, 8))}, 'MOV-01', 'schedule_move', 430000, 'VND', ${q('vt' + wDeXuat.slice(0, 8))}, NOW() + INTERVAL '90 min', ${q(wDeXuat)}::uuid, 'T1', 'ACTIVE')`)
psql(`INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status) VALUES (${q(crypto.randomUUID())}::uuid, ${q(wDeXuat)}::uuid, 'T1', ${q(quoteId)}::uuid, 'PROPOSED')`)

// ── 2. bảo trì đang chờ ĐƠN VỊ
const wBaoTri = gieoWorkflow('create_maintenance_request', { issue_type: 'plumbing', description: 'Vòi rò', preferred_date: '2026-12-01' })
psql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id) VALUES (${q(wBaoTri)}::uuid,'T1','create_maintenance_request','Bảo trì','{}'::jsonb,'AWAITING',${q(DON_VI.create_maintenance_request)})`)

// ── 3. tham quan đang chờ ĐƠN VỊ
const wThamQuan = gieoWorkflow('schedule_property_viewing', { project_id: 'PRJ-001', project_name: 'Vinhomes Ocean Park', viewing_date: '2026-12-01', viewing_time: '09:00', passenger_count: 2, wants_shuttle: false })
psql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id) VALUES (${q(wThamQuan)}::uuid,'T1','schedule_property_viewing','Lịch tham quan','{}'::jsonb,'AWAITING',${q(DON_VI.schedule_property_viewing)})`)

const b = await chromium.launch()
const page = await (await b.newContext({ viewport: { width: 1440, height: 1100 } })).newPage()
const jsErr = []
page.on('pageerror', (e) => jsErr.push(e.message))
await page.goto(APP + '/login')
await page.fill('#login-username', U); await page.fill('#login-password', PW)
await page.click('button[type=submit]'); await page.waitForTimeout(3000)

async function moChiTiet(wid) {
  await page.goto(`${APP}/workflow/${wid}`)
  await page.waitForTimeout(2500)
  return page.textContent('body')
}
const card = () => page.locator('[data-testid="customer-action"]')

console.log('\n  1 — chuyển nhà, chờ KHÁCH chọn đơn vị')
let than = await moChiTiet(wDeXuat)
check('có đúng một card hành động', (await card().count()) === 1)
check('đúng loại PROVIDER_PROPOSAL', (await card().getAttribute('data-action-kind')) === 'PROVIDER_PROPOSAL')
check('tiêu đề nói về đơn vị', than.includes('Xác nhận đơn vị cung cấp'))
check('KHÔNG nói chỗ đỗ xe', !than.includes('Chỗ đỗ xe'))
check('KHÔNG nói thanh toán', !than.includes('Xác nhận thanh toán'))
check('KHÔNG có tiêu đề "—"', !(await card().innerText()).split('\n')[0].includes('—'))
check('nêu tên đơn vị và giá', than.includes('Minh Phát') && /430[.,]000/.test(than))

console.log('\n  2 — bảo trì, chờ ĐƠN VỊ')
than = await moChiTiet(wBaoTri)
check('KHÔNG card hành động nào', (await card().count()) === 0)
check('KHÔNG nói chỗ đỗ xe', !than.includes('Chỗ đỗ xe'))

console.log('\n  3 — tham quan, chờ ĐƠN VỊ')
than = await moChiTiet(wThamQuan)
check('KHÔNG card hành động nào', (await card().count()) === 0)
check('KHÔNG nói chỗ đỗ xe', !than.includes('Chỗ đỗ xe'))

console.log('\n  4 — F5: cùng loại card, không rơi về thanh toán')
await moChiTiet(wDeXuat)
await page.reload(); await page.waitForTimeout(2500)
check('vẫn PROVIDER_PROPOSAL', (await card().getAttribute('data-action-kind')) === 'PROVIDER_PROPOSAL')
check('vẫn không nói chỗ đỗ xe', !(await page.textContent('body')).includes('Chỗ đỗ xe'))

console.log('\n  5 — sau khi đồng ý: card biến mất')
psql(`UPDATE service_provider_proposals SET status='CONFIRMED', confirmed_at=NOW() WHERE workflow_id=${q(wDeXuat)}::uuid`)
psql(`UPDATE service_quotes SET status='CONFIRMED', confirmed_at=NOW() WHERE workflow_id=${q(wDeXuat)}::uuid`)
await moChiTiet(wDeXuat)
check('không còn card hành động', (await card().count()) === 0)

console.log('\n  dọn dữ liệu canary')
for (const wid of wids) {
  for (const t of ['service_provider_proposals', 'service_quotes', 'service_approvals', 'approval_decisions', 'execution_logs',
                   'llm_usage', 'payment_approvals', 'workflow_clarifications', 'workflow_events',
                   'workflow_plan_revisions', 'workflow_repair_hints', 'workflow_tasks'])
    psql(`DELETE FROM ${t} WHERE workflow_id=${q(wid)}::uuid`)
  psql(`DELETE FROM workflows WHERE workflow_id=${q(wid)}::uuid`)
}
psql(`DELETE FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`)
check('không còn tài khoản canary', psql(`SELECT count(*) FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`) === '0')

console.log('\nlỗi JS trên trang:', jsErr.length ? jsErr.join(' | ') : 'không')
console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
