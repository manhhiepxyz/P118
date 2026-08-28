/**
 * AUDIT quyền sở hữu hàng đợi duyệt — đo qua HTTP + PostgreSQL của stack thật.
 *
 * Câu hỏi: sau `ddc3a93`, một đơn vị có thấy hay chạm được việc của đơn vị khác
 * không, và `total` có được tính SAU bộ lọc quyền sở hữu không.
 *
 * "Ẩn ở frontend" không tính là lọc. Nên bài này gọi thẳng API, không qua DOM:
 * nếu backend trả về dòng của đơn vị khác thì nó đã lộ, dù giao diện có vẽ ra
 * hay không.
 *
 * Chạy trên stack Docker (`p118_db`). Bài này TẠO tài khoản và yêu cầu mới,
 * KHÔNG sửa hay quyết định việc có sẵn.
 */
import { execFileSync } from 'node:child_process'

const API = process.env.P118_API ?? 'http://127.0.0.1:8000'
const PW = 'Passw0rd!123'
const psql = (q) =>
  execFileSync('docker', ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', 'p118_db', '-tAc', q],
    { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

const loi = []
const check = (t, ok, ct = '') => { console.log(`  ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}
async function taiKhoan(prefix, role, donVi = []) {
  const u = prefix + Math.floor(Math.random() * 1e7)
  await api('/auth/register', { method: 'POST', body: { username: u, password: PW } })
  if (role) psql(`UPDATE users SET role=${q(role)} WHERE username=${q(u)}`)
  for (const ma of donVi)
    psql(`INSERT INTO service_provider_accounts (user_id, service_provider_id)
          SELECT id, ${q(ma)} FROM users WHERE username=${q(u)} ON CONFLICT DO NOTHING`)
  const t = (await api('/auth/login', { method: 'POST', body: { username: u, password: PW } })).json?.access_token
  return { u, token: t }
}
/** Một việc AWAITING thuộc `ma`, gieo bằng SQL — tất định, không gọi model. */
function gieoViec(ownerUser, ma, nhan) {
  const wid = crypto.randomUUID()
  const uid = psql(`SELECT id FROM users WHERE username=${q(ownerUser)}`)
  psql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
        VALUES (${q(wid)}::uuid, ${q(nhan)}, 'WAITING_APPROVAL', ${q(uid)}::uuid)`)
  psql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)
        VALUES (${q(wid)}::uuid,'T1','schedule_move','WAITING_APPROVAL','[]'::jsonb,'{}'::jsonb)`)
  psql(`INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id)
        VALUES (${q(wid)}::uuid,'T1','schedule_move',${q(nhan)},'{}'::jsonb,'AWAITING',${q(ma)})`)
  return wid
}

const khach = await taiKhoan('audit_kh')
const A = await taiKhoan('audit_dv_a', 'provider', ['MOV-01'])
const B = await taiKhoan('audit_dv_b', 'provider', ['MOV-02'])
const admin = await taiKhoan('audit_admin', 'admin')

const vA = gieoViec(khach.u, 'MOV-01', 'Việc của A')
const vB = gieoViec(khach.u, 'MOV-02', 'Việc của B')
const tongBang = Number(psql("SELECT count(*) FROM service_approvals WHERE status='AWAITING'"))
console.log(`\nBảng có ${tongBang} dòng AWAITING toàn hệ thống. A giữ MOV-01, B giữ MOV-02.\n`)

// ── 1. A không thấy gì của B
console.log('1 — A đọc hàng đợi')
const rA = await api('/service-approvals?status=AWAITING', { token: A.token })
const idA = (rA.json.items ?? []).map((m) => m.workflow_id)
const maA = new Set((rA.json.items ?? []).map((m) => m.service_provider_id))
check('A thấy việc của mình', idA.includes(vA))
check('A KHÔNG thấy việc của B', !idA.includes(vB))
check('mọi dòng A nhận đều là MOV-01', [...maA].every((m) => m === 'MOV-01'), [...maA].join(','))
check('total của A tính SAU bộ lọc', rA.json.total === idA.length, `total=${rA.json.total} items=${idA.length}`)
check('total của A KHÔNG phải tổng toàn bảng', rA.json.total !== tongBang, `${rA.json.total} vs ${tongBang}`)

// ── 2. B không thấy gì của A
console.log('\n2 — B đọc hàng đợi')
const rB = await api('/service-approvals?status=AWAITING', { token: B.token })
const idB = (rB.json.items ?? []).map((m) => m.workflow_id)
check('B thấy việc của mình', idB.includes(vB))
check('B KHÔNG thấy việc của A', !idB.includes(vA))
check('total của B tính SAU bộ lọc', rB.json.total === idB.length, `total=${rB.json.total} items=${idB.length}`)

// Lịch sử cũng phải lọc — một cổng đọc thứ hai là một chỗ để quên.
const hA = await api('/service-approvals?status=decided', { token: A.token })
check('lịch sử của A cũng lọc theo đơn vị',
  (hA.json.items ?? []).every((m) => m.service_provider_id === 'MOV-01'),
  `${(hA.json.items ?? []).length} dòng`)
check('total lịch sử cũng tính sau lọc', hA.json.total === (hA.json.items ?? []).length,
  `total=${hA.json.total} items=${(hA.json.items ?? []).length}`)

// ── 3. A quyết định việc của B
console.log('\n3 — A cố quyết định việc của B')
const d = await api(`/service-approvals/${vB}/T1/decide`, { token: A.token, method: 'POST', body: { decision: 'approve' } })
check('A bị chặn khi quyết định việc của B', d.status === 404, `http ${d.status}`)
check('404 chứ không 403 (403 xác nhận dòng ấy tồn tại)', d.status !== 403, `http ${d.status}`)
check('việc của B không bị đụng',
  psql(`SELECT status FROM service_approvals WHERE workflow_id=${q(vB)}::uuid`) === 'AWAITING')

// ── 4. Admin
console.log('\n4 — Admin')
const aQ = await api('/service-approvals?status=AWAITING', { token: admin.token })
check('admin KHÔNG vào được hàng đợi đơn vị', aQ.status === 403, `http ${aQ.status}`)
const aD = await api(`/service-approvals/${vA}/T1/decide`, { token: admin.token, method: 'POST', body: { decision: 'approve' } })
check('admin KHÔNG quyết định được', aD.status === 403, `http ${aD.status}`)
const aM = await api('/admin/metrics', { token: admin.token })
check('admin xem được tổng hợp qua /admin', aM.status === 200, `http ${aM.status}`)
const aW = await api('/admin/requests?limit=5', { token: admin.token })
check('admin xem được danh sách yêu cầu qua /admin/requests', aW.status === 200, `http ${aW.status}`)
// Giám sát phải NÓI được ai đang giữ mỗi bước, nếu không con số "đang chờ đơn
// vị" là một con số không truy được về ai.
const mot = (aW.json?.items ?? [])[0]
const ct = mot ? await api(`/admin/requests/${mot.workflow_id}`, { token: admin.token }) : { status: 0, json: null }
check('chi tiết giám sát nói rõ đơn vị nào giữ bước nào',
  ct.status === 200 && JSON.stringify(ct.json).includes('service_provider'), `http ${ct.status}`)
// Giám sát là CHỈ ĐỌC: không có đường quyết định nào cho admin.
const adDecide = await api(`/admin/requests/${mot?.workflow_id}/decide`, { token: admin.token, method: 'POST', body: {} })
check('/admin không có đường quyết định', adDecide.status === 404 || adDecide.status === 405, `http ${adDecide.status}`)
const pM = await api('/admin/metrics', { token: A.token })
check('provider KHÔNG vào được /admin', pM.status === 403, `http ${pM.status}`)

// ── 5. Cổng đọc thứ hai: /viewing-approvals
console.log('\n5 — Cổng đọc thứ hai (/viewing-approvals)')
const vwA = await api('/viewing-approvals', { token: A.token })
const vwB = await api('/viewing-approvals', { token: B.token })
if (vwA.status === 200 && vwB.status === 200) {
  const sA = JSON.stringify(vwA.json?.items ?? [])
  const sB = JSON.stringify(vwB.json?.items ?? [])
  check('/viewing-approvals lọc theo đơn vị', sA !== sB || (vwA.json.items ?? []).length === 0,
    `A nhận ${(vwA.json.items ?? []).length} dòng, B nhận ${(vwB.json.items ?? []).length} dòng — giống hệt nhau`)
} else {
  check('/viewing-approvals trả lời được', false, `A http ${vwA.status}, B http ${vwB.status}`)
}
const vwAd = await api('/viewing-approvals', { token: admin.token })
check('admin KHÔNG vào được /viewing-approvals', vwAd.status === 403, `http ${vwAd.status}`)

console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
