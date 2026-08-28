/**
 * AUDIT cờ `SERVICE_PROVIDER_MATCHING` — đo PHẠM VI, không đọc mã rồi tin.
 *
 * Hai câu hỏi:
 *   1. bật (=1): có ĐÚNG `schedule_move` đi đường mới không, hay dịch vụ khác
 *      cũng bị kéo theo;
 *   2. tắt (=0): `schedule_move` có về đúng đường cũ không — đơn vị mặc định,
 *      hàng đợi mở ngay, không báo giá, không đề xuất, không nút chọn lại.
 *
 * Nhận trạng thái cờ qua argv vì bài này KHÔNG tự đổi cờ: đổi cờ là dựng lại
 * container, và một bài kiểm tự dựng lại hạ tầng nó đang đo là một bài kiểm
 * không ai đọc được kết quả.
 *
 *   node audit_flag_scope.mjs 1     (sau khi bật cờ và `docker compose up -d backend`)
 *   node audit_flag_scope.mjs 0
 */
import { execFileSync } from 'node:child_process'

const CO = process.argv[2]
if (CO !== '0' && CO !== '1') { console.log('DỪNG: truyền 0 hoặc 1'); process.exit(2) }
const API = process.env.P118_API ?? 'http://127.0.0.1:8000'
const PW = 'Passw0rd!123'
const psql = (q) => execFileSync('docker', ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', 'p118_db', '-tAc', q], { encoding: 'utf8' }).trim()
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

const thay = docker_env()
function docker_env() {
  try { return execFileSync('docker', ['exec', 'p118_backend', 'printenv', 'SERVICE_PROVIDER_MATCHING'], { encoding: 'utf8' }).trim() }
  catch { return '(chưa đặt)' }
}
if (thay !== CO) { console.log(`DỪNG: container đang thấy cờ = ${thay}, không phải ${CO}. Chạy 'docker compose up -d backend' sau khi sửa .env.`); process.exit(2) }

const loi = []
const check = (t, ok, ct = '') => { console.log(`  ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, { method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) }, body: body ? JSON.stringify(body) : undefined })
  return { status: r.status, json: await r.json().catch(() => null) }
}
const ngu = (ms) => new Promise((r) => setTimeout(r, ms))
const DUNG = new Set(['WAITING_PROVIDER_PROPOSAL', 'WAITING_PROVIDER_RESELECTION', 'WAITING_SERVICE_APPROVAL', 'WAITING_APPROVAL', 'PAYMENT_APPROVAL_REQUIRED', 'CHAT',
  'SUCCESS', 'FAILED', 'CANCELLED', 'NEEDS_INFORMATION', 'PLANNING_ERROR', 'VALIDATION_ERROR', 'EXECUTION_ERROR'])
const cho = async (w, t) => { for (let i = 0; i < 150; i++) { const v = (await api(`/workflows/demo/${w}`, { token: t })).json ?? {}; if (DUNG.has(v.stage) || DUNG.has(v.status)) return v; await ngu(1000) } throw new Error('treo') }

const U = 'flag' + CO + '_' + Math.floor(Math.random() * 1e6)
await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
const tok = (await api('/auth/login', { method: 'POST', body: { username: U, password: PW } })).json.access_token

const NGAY = new Date(Date.now() + 50 * 86400000).toISOString().slice(0, 10)
const dem = (wid, bang) => Number(psql(`SELECT count(*) FROM ${bang} WHERE workflow_id=${q(wid)}::uuid`))

console.log(`\n=== Cờ = ${CO} · chuyển nhà (dịch vụ DUY NHẤT được cờ chạm) ===`)
const b1 = await api('/workflows/demo/start', { token: tok, method: 'POST', body: { goal: `Mình muốn đặt lịch chuyển nhà ngày ${NGAY} lúc 8 giờ sáng, đi xe tải nhỏ, không cần thang máy và không cần người bốc vác.` } })
const w1 = b1.json.workflow_id
const v1 = await cho(w1, tok)
const ma1 = psql(`SELECT COALESCE(service_provider_id,'—') FROM service_approvals WHERE workflow_id=${q(w1)}::uuid`)
console.log(`  workflow ${w1.slice(0, 8)} · stage ${v1.stage} · đơn vị ${ma1 || '(chưa mở hàng đợi)'}`)

if (CO === '1') {
  check('dừng ở đề xuất, chưa hỏi đơn vị nào', v1.stage === 'WAITING_PROVIDER_PROPOSAL', v1.stage)
  check('đã sinh báo giá', dem(w1, 'service_quotes') > 0, `${dem(w1, 'service_quotes')} báo giá`)
  check('đã sinh đề xuất', dem(w1, 'service_provider_proposals') === 1)
  check('CHƯA mở hàng đợi cho đơn vị nào', dem(w1, 'service_approvals') === 0)
} else {
  check('đi thẳng vào hàng đợi đơn vị', v1.stage === 'WAITING_SERVICE_APPROVAL', v1.stage)
  check('KHÔNG sinh báo giá nào', dem(w1, 'service_quotes') === 0, `${dem(w1, 'service_quotes')}`)
  check('KHÔNG sinh đề xuất nào', dem(w1, 'service_provider_proposals') === 0, `${dem(w1, 'service_provider_proposals')}`)
  check('dùng đơn vị MẶC ĐỊNH của provider_directory', ma1 === 'MOV-01', ma1)
  check('màn hình không có đề xuất nào để bấm', !(v1.service_proposals ?? []).length)
}

console.log(`\n=== Cờ = ${CO} · một dịch vụ KHÁC (chỗ đỗ xe) — phải legacy ở CẢ HAI trạng thái ===`)
const b2 = await api('/workflows/demo/start', { token: tok, method: 'POST', body: { goal: `Mình muốn đặt chỗ đỗ xe ngày ${NGAY} ở khu A cho xe ô tô biển 51H-12345.` } })
const w2 = b2.json.workflow_id
const v2 = await cho(w2, tok)
const cacTool = psql(`SELECT string_agg(DISTINCT tool, ',') FROM workflow_tasks WHERE workflow_id=${q(w2)}::uuid`)
console.log(`  workflow ${w2.slice(0, 8)} · stage ${v2.stage} · tool ${cacTool}`)
if (cacTool.includes('schedule_move')) {
  check('kế hoạch không lẫn chuyển nhà', false, cacTool)
} else {
  check('KHÔNG sinh báo giá', dem(w2, 'service_quotes') === 0, `${dem(w2, 'service_quotes')}`)
  check('KHÔNG sinh đề xuất', dem(w2, 'service_provider_proposals') === 0, `${dem(w2, 'service_provider_proposals')}`)
  check('không có thẻ đề xuất nào cho khách bấm', !(v2.service_proposals ?? []).length)
  check('không có bước chờ khách chọn đơn vị', v2.stage !== 'WAITING_PROVIDER_PROPOSAL', v2.stage)
}

console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
