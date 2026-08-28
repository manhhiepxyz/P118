/**
 * CANARY qua MODEL THẬT: hỏi thêm về đề xuất đơn vị, trên `p118_e2e_db`.
 *
 * Bộ kiểm PostgreSQL ghim nhãn ý định để đo phần còn lại một cách tất định.
 * Bài này làm việc ngược lại: để MÔ HÌNH đọc câu tiếng Việt thật, và chỉ kiểm
 * những điều đúng bất kể nó phân loại thế nào —
 *
 *   * không sinh workflow thứ hai;
 *   * câu trả lời nói về báo giá của chính yêu cầu ấy;
 *   * không có chữ nào của bất động sản;
 *   * đổi đơn vị bằng LỜI thì thẻ đổi theo, và F5 vẫn giữ.
 *
 * Dọn sạch dữ liệu canary sau khi chụp bằng chứng. `p118_db` không bị đụng.
 */
import { execFileSync } from 'node:child_process'

const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const PW = 'Passw0rd!123'
const HAU_TO = 'pf' + Math.floor(Math.random() * 1e6)

const psql = (s) => execFileSync('psql', ['-d', DB, '-tAc', s], { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`
if (psql('SELECT current_database()') !== DB) { console.log('DỪNG: sai database'); process.exit(2) }

const loi = []
const check = (t, ok, ct = '') => { console.log(`    ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, { method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined })
  return { status: r.status, json: await r.json().catch(() => null) }
}
const ngu = (ms) => new Promise((r) => setTimeout(r, ms))
const DUNG = new Set(['WAITING_PROVIDER_PROPOSAL', 'WAITING_SERVICE_APPROVAL', 'SUCCESS', 'FAILED', 'NEEDS_INFORMATION', 'CHAT', 'PLANNING_ERROR', 'VALIDATION_ERROR', 'EXECUTION_ERROR'])
const cho = async (w, t) => { for (let i = 0; i < 150; i++) { const v = (await api(`/workflows/demo/${w}`, { token: t })).json ?? {}; if (DUNG.has(v.stage) || DUNG.has(v.status)) return v; await ngu(1000) } throw new Error('treo') }

const U = `kh_${HAU_TO}`
await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
const tok = (await api('/auth/login', { method: 'POST', body: { username: U, password: PW } })).json.access_token
const uid = psql(`SELECT id FROM users WHERE username=${q(U)}`)

const NGAY = new Date(Date.now() + 55 * 86400000).toISOString().slice(0, 10)
const CAU_DAU = `Mình muốn đặt lịch chuyển nhà ngày ${NGAY} lúc 8 giờ sáng, đi xe tải nhỏ, không cần thang máy và không cần người bốc vác.`
console.log(`\ncâu khách gõ: ${CAU_DAU}\n`)

const bd = await api('/workflows/demo/start', { token: tok, method: 'POST', body: { goal: CAU_DAU } })
const wid = bd.json.workflow_id
const sid = bd.json.session_id
let v = await cho(wid, tok)
check('model dừng ở bước chọn đơn vị', v.stage === 'WAITING_PROVIDER_PROPOSAL', v.stage)
const dauTien = v.customer_action
console.log(`  đề xuất đầu: ${dauTien?.provider?.name} · ${dauTien?.amount}`)

const demWorkflow = () => Number(psql(`SELECT count(*) FROM workflows WHERE session_id=${q(sid)}`))
const donViDangDeXuat = () => psql(`SELECT q.service_provider_id FROM service_provider_proposals p JOIN service_quotes q ON q.quote_id=p.quote_id WHERE p.workflow_id=${q(wid)}::uuid AND p.status='PROPOSED'`)
const BAT_DONG_SAN = ['Vinhomes', 'dự án', 'căn hộ', 'tham quan']

async function hoi(nhan, cau) {
  const truoc = demWorkflow()
  const r = await api('/workflows/demo/start', { token: tok, method: 'POST', body: { goal: cau, session_id: sid } })
  const traLoi = r.json?.answer ?? r.json?.message ?? ''
  console.log(`\n  ${nhan} — "${cau}"`)
  console.log(`    → ${traLoi.slice(0, 130)}`)
  check('không sinh workflow thứ hai', demWorkflow() === truoc, `${truoc} → ${demWorkflow()}`)
  check('trả về chính yêu cầu đang chờ', r.json?.workflow_id === wid, r.json?.workflow_id)
  check('không có chữ nào của bất động sản', !BAT_DONG_SAN.some((c) => traLoi.toLowerCase().includes(c.toLowerCase())))
  return { traLoi, body: r.json }
}

const gia = psql(`SELECT string_agg(amount::text, ',' ORDER BY amount) FROM service_quotes WHERE workflow_id=${q(wid)}::uuid`).split(',')
const { traLoi: reHon } = await hoi('rẻ hơn', 'còn chỗ nào rẻ hơn không')
check('nhắc tới một con số có thật trong bảng báo giá', gia.some((g) => reHon.includes(Number(g).toLocaleString('de-DE'))), gia.join('/'))

await hoi('uy tín', 'đơn vị này uy tín không')
const { traLoi: soSanh } = await hoi('so sánh', 'so sánh các bên giúp tôi')
check('so sánh nêu nhiều hơn một đơn vị', (soSanh.match(/Minh Phát|Đại Tín|An Khang/g) || []).length >= 2, soSanh.slice(0, 90))

const truocKhiDoi = donViDangDeXuat()
const khac = { 'MOV-01': 'Đại Tín', 'MOV-02': 'An Khang', 'MOV-03': 'Minh Phát' }[truocKhiDoi]
const { body: sauDoi } = await hoi('đổi đơn vị bằng lời', `đổi sang ${khac}`)
const sauKhiDoi = donViDangDeXuat()
check('đề xuất đã đổi sang đơn vị khách gọi tên', sauKhiDoi !== truocKhiDoi, `${truocKhiDoi} → ${sauKhiDoi}`)
check('thẻ trên màn hình đổi theo', sauDoi?.customer_action?.provider?.name?.includes(khac), sauDoi?.customer_action?.provider?.name)
check('đề xuất cũ thành SUPERSEDED', psql(`SELECT count(*) FROM service_provider_proposals WHERE workflow_id=${q(wid)}::uuid AND status='SUPERSEDED'`) === '1')
check('CHƯA mở hàng đợi cho đơn vị nào', psql(`SELECT count(*) FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === '0')

const lai = (await api(`/workflows/demo/${wid}`, { token: tok })).json
check('F5 vẫn giữ đúng đơn vị mới', lai?.customer_action?.provider?.name === sauDoi?.customer_action?.provider?.name, lai?.customer_action?.provider?.name)

console.log('\n  xác nhận qua nút (cùng cửa với lời)')
const r = await api(`/service-proposals/${lai.customer_action.proposal_id}/confirm`, { token: tok, method: 'POST', body: { decision: 'confirm' } })
check('confirm được', r.status === 200, `http ${r.status}`)
await cho(wid, tok)
check('đúng đơn vị khách chọn nhận việc', psql(`SELECT service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`) === sauKhiDoi)

console.log('\n  dọn dữ liệu canary')
for (const w of psql(`SELECT workflow_id FROM workflows WHERE session_id=${q(sid)}`).split('\n').filter(Boolean)) {
  for (const t of ['service_provider_proposals', 'service_quotes', 'service_approvals', 'approval_decisions', 'execution_logs',
                   'llm_usage', 'payment_approvals', 'workflow_clarifications', 'workflow_events',
                   'workflow_plan_revisions', 'workflow_repair_hints', 'workflow_tasks'])
    psql(`DELETE FROM ${t} WHERE workflow_id=${q(w)}::uuid`)
  psql(`DELETE FROM workflows WHERE workflow_id=${q(w)}::uuid`)
}
psql(`DELETE FROM sessions WHERE session_id=${q(sid)}`)
psql(`DELETE FROM users WHERE username=${q(U)}`)
check('không còn tài khoản canary', psql(`SELECT count(*) FROM users WHERE username=${q(U)}`) === '0')

console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
