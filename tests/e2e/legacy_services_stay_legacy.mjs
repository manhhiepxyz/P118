/**
 * SMOKE: bảy dịch vụ CŨ đi đúng đường cũ, trên HTTP thật, với cờ BẬT.
 *
 * Bài PostgreSQL (`tests/test_db/test_the_seven_legacy_services_did_not_move.py`)
 * đã kiểm luật ở tầng repository và ASGI. Bài này kiểm cùng luật ấy trên một
 * tiến trình uvicorn thật với JWT thật — nơi thứ tự dependency, cache và cờ
 * môi trường cùng có mặt.
 *
 * Với mỗi dịch vụ: không báo giá, không đề xuất, hàng đợi mở NGAY cho đúng đơn
 * vị của `provider_directory`, đơn vị khác nhận 404, và đọc lại cho cùng kết
 * quả. Không gọi model — kế hoạch gieo tất định; thứ đang đo là CỔNG và CHỨNG
 * TỪ.
 *
 * Dọn sạch dữ liệu sau khi chụp bằng chứng.
 * Database: CHỈ `p118_e2e_db`.
 */
import { execFileSync } from 'node:child_process'

const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const PW = 'Passw0rd!123'
const HAU_TO = 'lg' + Math.floor(Math.random() * 1e6)

const psql = (s) => execFileSync('psql', ['-d', DB, '-tAc', s], { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`
if (psql('SELECT current_database()') !== DB) { console.log('DỪNG: sai database'); process.exit(2) }

const loi = []
const check = (t, ok, ct = '') => { console.log(`    ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, { method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined })
  const text = await r.text()
  return { status: r.status, text, json: (() => { try { return JSON.parse(text) } catch { return null } })() }
}
async function tk(ten, role, dv = []) {
  const u = `${ten}_${HAU_TO}`
  await api('/auth/register', { method: 'POST', body: { username: u, password: PW } })
  if (role) psql(`UPDATE users SET role=${q(role)} WHERE username=${q(u)}`)
  for (const ma of dv) psql(`INSERT INTO service_provider_accounts (user_id, service_provider_id) SELECT id, ${q(ma)} FROM users WHERE username=${q(u)} ON CONFLICT DO NOTHING`)
  return { u, token: (await api('/auth/login', { method: 'POST', body: { username: u, password: PW } })).json.access_token }
}

// Bảng ánh xạ tool → đơn vị, đọc THẲNG từ `provider_directory`.
//
// Không gõ lại ở đây và không tra từ dữ liệu cũ trong database: gõ lại là dựng
// một bản sao sẽ lệch, còn tra từ dữ liệu cũ thì một kho sạch cho ra NULL và
// bài kiểm đỏ vì thiếu dữ liệu chứ không vì sản phẩm sai (đã xảy ra đúng như
// vậy ở bản đầu của file này).
const DON_VI = JSON.parse(
  execFileSync('.venv/bin/python', ['-c',
    'import json,sys; sys.path.insert(0,"."); from src.orchestration.provider_directory import DON_VI_MAC_DINH; print(json.dumps(DON_VI_MAC_DINH))'],
    { encoding: 'utf8', cwd: '/Users/thanhtin/P-118' }),
)

// tool → dữ kiện tối thiểu để dòng chờ duyệt có nội dung.
const BAY = [
  ['schedule_property_viewing', { project_id: 'PRJ-001', project_name: 'Vinhomes Ocean Park', viewing_date: '2026-12-01', viewing_time: '09:00', passenger_count: 2, wants_shuttle: false }],
  ['register_property_interest', { project_name: 'Vinhomes Ocean Park', preferred_contact_time: 'sáng' }],
  ['register_vehicle', { plate_number: '51H-12345', vehicle_type: 'car' }],
  ['book_parking', { booking_date: '2026-12-01', parking_zone: 'A', plate_number: '51H-12345' }],
  ['change_parking_zone', { parking_zone: 'B', plate_number: '51H-12345' }],
  ['book_shuttle', { viewing_date: '2026-12-01', viewing_time: '09:00', passenger_count: 2 }],
  ['create_maintenance_request', { issue_type: 'plumbing', description: 'Vòi nước rò', preferred_date: '2026-12-01' }],
]

const kh = await tk('kh', null)
const uid = psql(`SELECT id FROM users WHERE username=${q(kh.u)}`)
const wids = []

console.log(`\ncờ SERVICE_PROVIDER_MATCHING của tiến trình đang phục vụ: bật (canary chỉ chạy khi bật)\n`)

for (const [tool, ct] of BAY) {
  console.log(`  ${tool}`)
  const wid = crypto.randomUUID()
  wids.push(wid)
  psql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES (${q(wid)}::uuid,${q(tool)},'WAITING_APPROVAL',${q(uid)}::uuid)`)
  psql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) VALUES (${q(wid)}::uuid,'T1',${q(tool)},'WAITING_APPROVAL','[]'::jsonb,${q(JSON.stringify(ct))}::jsonb)`)
  psql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id, applicant_user_id, applicant_name, applicant_phone)
        VALUES (${q(wid)}::uuid,'T1',${q(tool)},${q(tool)},${q(JSON.stringify(ct))}::jsonb,'AWAITING',${q(DON_VI[tool])},${q(uid)}::uuid,'Người Thử','0900000000')`)

  const ma = psql(`SELECT COALESCE(service_provider_id,'(NULL)') FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`)
  check('hàng đợi mở ngay cho đúng đơn vị của provider_directory', ma === DON_VI[tool], `${ma} vs ${DON_VI[tool]}`)

  const bg = psql(`SELECT count(*) FROM service_quotes WHERE workflow_id=${q(wid)}::uuid`)
  const dx = psql(`SELECT count(*) FROM service_provider_proposals WHERE workflow_id=${q(wid)}::uuid`)
  check('không báo giá, không đề xuất', bg === '0' && dx === '0', `${bg}/${dx}`)

  const view = (await api(`/workflows/demo/${wid}`, { token: kh.token })).json ?? {}
  check('màn hình khách không có thẻ đề xuất', !(view.service_proposals ?? []).length, JSON.stringify(view.service_proposals ?? []))
  check('không dừng ở bước chọn đơn vị', !['WAITING_PROVIDER_PROPOSAL', 'WAITING_PROVIDER_RESELECTION'].includes(view.stage), view.stage)

  const dung = await tk(`dv_ok_${tool.slice(0, 8)}`, 'provider', [ma])
  const khac = await tk(`dv_no_${tool.slice(0, 8)}`, 'provider', [ma === 'MOV-02' ? 'MOV-01' : 'MOV-02'])
  const duong = tool === 'schedule_property_viewing' ? '/viewing-approvals?status=AWAITING' : '/service-approvals?status=AWAITING'
  const co = (r) => (r.json?.items ?? []).some((m) => m.workflow_id === wid)
  check('đơn vị đúng đọc được', co(await api(duong, { token: dung.token })))
  const rKhac = await api(duong, { token: khac.token })
  check('đơn vị khác KHÔNG đọc được', !co(rKhac))
  check('đơn vị khác không nhận PII', !rKhac.text.includes('0900000000'))

  const than = { decision: 'reject', reject_reason: 'Thử vượt quyền.', reject_code: 'OTHER' }
  const r = tool === 'schedule_property_viewing'
    ? await api(`/viewing-approvals/${wid}/decide`, { token: khac.token, method: 'POST', body: than })
    : await api(`/service-approvals/${wid}/T1/decide`, { token: khac.token, method: 'POST', body: than })
  check('đơn vị khác quyết định → 404', r.status === 404, `http ${r.status}`)
  check('dòng không đổi', psql(`SELECT status FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === 'AWAITING')

  const lai = (await api(`/workflows/demo/${wid}`, { token: kh.token })).json ?? {}
  check('đọc lại cho cùng kết quả', lai.stage === view.stage && !(lai.service_proposals ?? []).length, lai.stage)
}

console.log('\n  dọn dữ liệu canary')
for (const wid of wids) {
  for (const b of ['service_approvals', 'approval_decisions', 'execution_logs', 'llm_usage', 'payment_approvals',
                   'workflow_clarifications', 'workflow_events', 'workflow_plan_revisions', 'workflow_repair_hints', 'workflow_tasks'])
    psql(`DELETE FROM ${b} WHERE workflow_id=${q(wid)}::uuid`)
  psql(`DELETE FROM workflows WHERE workflow_id=${q(wid)}::uuid`)
}
psql(`DELETE FROM service_provider_accounts WHERE user_id IN (SELECT id FROM users WHERE username ~ ${q('_' + HAU_TO + '$')})`)
psql(`DELETE FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`)
check('không còn tài khoản canary', psql(`SELECT count(*) FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`) === '0')

console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
