/**
 * CANARY quyền sở hữu cổng tham quan — trên đường HTTP thật, `p118_e2e_db`.
 *
 * Bài PostgreSQL đã kiểm luật; bài này kiểm rằng luật ấy còn đúng khi đi qua
 * uvicorn, JWT thật và một tiến trình backend thật — nơi `require_roles`, thứ
 * tự dependency và bộ nhớ cache cùng có mặt.
 *
 * Cấu trúc giống hệt lỗ hổng đã đo được trước khi sửa, để so sánh trực tiếp:
 * đơn vị B (chuyển nhà) đọc và quyết định một lịch tham quan của BQL-SALES.
 *
 * TỪ CHỐI chứ không duyệt: duyệt gọi Tour provider thật và đặt xe.
 * Dọn sạch dữ liệu canary sau khi chụp bằng chứng.
 *
 * Database: CHỈ `p118_e2e_db`.
 */
import { execFileSync } from 'node:child_process'

const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const PW = 'Passw0rd!123'
const HAU_TO = 'vqs' + Math.floor(Math.random() * 1e6)

const psql = (q) => execFileSync('psql', ['-d', DB, '-tAc', q], { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`
if (psql('SELECT current_database()') !== DB) { console.log('DỪNG: sai database'); process.exit(2) }

const loi = []
const check = (t, ok, ct = '') => { console.log(`  ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
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

const TEN = `Canary ${HAU_TO}`
const SDT = '0900777888'

const kh = await tk('kh', null)
const A = await tk('dv_sales', 'provider', ['BQL-SALES'])
const B = await tk('dv_move', 'provider', ['MOV-02'])
const C = await tk('dv_nhieu', 'provider', ['MOV-01', 'MOV-02', 'FIX-01'])
const AD = await tk('ad', 'admin')

const wid = crypto.randomUUID()
const uid = psql(`SELECT id FROM users WHERE username=${q(kh.u)}`)
const ct = '{"project_id":"VH-SGP","project_name":"Vinhomes Sài Gòn Park","viewing_date":"2026-12-01","viewing_time":"09:00","passenger_count":2,"wants_shuttle":false}'
psql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id) VALUES (${q(wid)}::uuid,'Canary tham quan','WAITING_APPROVAL',${q(uid)}::uuid)`)
psql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data) VALUES (${q(wid)}::uuid,'T1','schedule_property_viewing','WAITING_APPROVAL','[]'::jsonb,${q(ct)}::jsonb)`)
psql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id, applicant_user_id, applicant_name, applicant_phone)
      VALUES (${q(wid)}::uuid,'T1','schedule_property_viewing','Lịch tham quan',${q(ct)}::jsonb,'AWAITING','BQL-SALES',${q(uid)}::uuid,${q(TEN)},${q(SDT)})`)
console.log(`\ngieo 1 lịch tham quan thuộc BQL-SALES · workflow ${wid.slice(0, 8)}`)
console.log(`A=BQL-SALES · B=MOV-02 · C=MOV-01,MOV-02,FIX-01 (không có BQL-SALES)\n`)

const co = (r) => (r.json?.items ?? []).some((m) => m.workflow_id === wid)

console.log('1 — đọc')
const rA = await api('/viewing-approvals?status=AWAITING', { token: A.token })
check('A (sở hữu) thấy lịch của mình', co(rA))
for (const [ten, t] of [['B', B.token], ['C', C.token]]) {
  const r = await api('/viewing-approvals?status=AWAITING', { token: t })
  const rls = await api('/viewing-approvals', { token: t })
  check(`${ten} KHÔNG thấy trong hàng đợi`, !co(r))
  check(`${ten} KHÔNG thấy trong lịch sử`, !co(rls))
  check(`${ten} KHÔNG đọc được PII`, !r.text.includes(TEN) && !r.text.includes(SDT) && !rls.text.includes(TEN))
}
check('A đọc được PII (họ cần gọi khách)', rA.text.includes(TEN))

console.log('\n2 — quyết định trái quyền')
const than = { decision: 'reject', reject_reason: 'Canary vượt quyền.', reject_code: 'OTHER' }
for (const [ten, t] of [['B', B.token], ['C', C.token]]) {
  const r = await api(`/viewing-approvals/${wid}/decide`, { token: t, method: 'POST', body: than })
  check(`${ten} bị chặn`, r.status === 404, `http ${r.status}`)
  check(`${ten} không đọc được PII qua câu báo lỗi`, !r.text.includes(TEN) && !r.text.includes(SDT))
}
const la = await api(`/viewing-approvals/${crypto.randomUUID()}/decide`, { token: B.token, method: 'POST', body: than })
check('id lạ và id của người khác trả lời GIỐNG NHAU', la.status === 404 && la.text === (await api(`/viewing-approvals/${wid}/decide`, { token: B.token, method: 'POST', body: than })).text)
check('dòng KHÔNG đổi sau các lượt trái quyền', psql(`SELECT status FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === 'AWAITING')
check('không ai ký vào dòng ấy', psql(`SELECT COALESCE(decided_by,'—') FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === '—')

console.log('\n3 — admin')
check('admin KHÔNG đọc được hàng đợi tham quan', (await api('/viewing-approvals', { token: AD.token })).status === 403)
check('admin KHÔNG quyết định được', (await api(`/viewing-approvals/${wid}/decide`, { token: AD.token, method: 'POST', body: than })).status === 403)
check('admin xem được giám sát', (await api('/admin/requests?limit=3', { token: AD.token })).status === 200)
check('provider KHÔNG vào được giám sát', (await api('/admin/requests', { token: A.token })).status === 403)

console.log('\n4 — đơn vị đúng vẫn làm được việc')
const ok = await api(`/viewing-approvals/${wid}/decide`, { token: A.token, method: 'POST', body: { decision: 'reject', reject_reason: 'Khu này kín lịch ngày đó.', reject_code: 'NO_AVAILABILITY' } })
check('A từ chối được', ok.status === 200, `http ${ok.status}`)
check('dòng đã chuyển REJECTED', psql(`SELECT status FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === 'REJECTED')
check('ký đúng tên A', psql(`SELECT decided_by FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === A.u)

// ── dọn
console.log('\n5 — dọn dữ liệu canary')
// Xoá theo ĐÚNG thứ tự khoá ngoại. Danh sách đọc từ
// `information_schema` chứ không nhớ theo trí nhớ: lượt từ chối
// `NO_AVAILABILITY` mở vòng hỏi lại và ghi thêm `workflow_clarifications` —
// một bảng con mà bản đầu của bài này không biết là có.
for (const bang of ['service_approvals', 'approval_decisions', 'execution_logs', 'llm_usage',
                    'payment_approvals', 'workflow_clarifications', 'workflow_events',
                    'workflow_plan_revisions', 'workflow_repair_hints', 'workflow_tasks'])
  psql(`DELETE FROM ${bang} WHERE workflow_id=${q(wid)}::uuid`)
psql(`DELETE FROM workflows WHERE workflow_id=${q(wid)}::uuid`)
// Xoá theo HẬU TỐ chính xác của lượt chạy này, không dùng LIKE với `_`
// (`_` là ký tự đại diện một ký tự trong LIKE — đúng cái đã từng xoá nhầm
// hàng loạt tài khoản provider).
psql(`DELETE FROM service_provider_accounts WHERE user_id IN (SELECT id FROM users WHERE username ~ ${q('_' + HAU_TO + '$')})`)
psql(`DELETE FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`)
check('không còn workflow canary', psql(`SELECT count(*) FROM workflows WHERE workflow_id=${q(wid)}::uuid`) === '0')
check('không còn tài khoản canary', psql(`SELECT count(*) FROM users WHERE username ~ ${q('_' + HAU_TO + '$')}`) === '0')

console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
