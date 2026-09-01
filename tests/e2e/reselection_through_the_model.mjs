/**
 * Canary qua MODEL THẬT: một câu tiếng Việt tự nhiên → kế hoạch → đề xuất đơn
 * vị A → khách đồng ý → A TỪ CHỐI → khách bấm tìm đơn vị khác → đề xuất B →
 * khách đồng ý → B duyệt → SUCCESS.
 *
 * Vì sao cần dù đã có canary gieo SQL: canary gieo SQL bắt đầu từ một trạng
 * thái do chính nó dựng, nên nó không kiểm được nửa đầu — model có sinh ra
 * `schedule_move` với đủ ô không, và luồng đề xuất có nối được vào kế hoạch
 * thật không. Ở đây KHÔNG ô nào được gieo bằng SQL trước khi model chạy.
 *
 * Bài đối chứng nằm cùng file: cùng một đường, nhưng đơn vị từ chối bằng
 * `INVALID_REQUEST`. Kết quả PHẢI khác — không nút "tìm đơn vị khác", không
 * lần thử mới, không tự đổi đơn vị.
 *
 * Chạy: backend trên p118_e2e_db cổng 8100, cờ SERVICE_PROVIDER_MATCHING=1.
 *   node reselection_through_the_model.mjs
 *
 * Database: CHỈ `p118_e2e_db`. `p118_db` được đếm trước/sau và phải không đổi.
 */

import { execFileSync } from 'node:child_process'

const PW = 'Passw0rd!123'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'

const sql = (db, q) => execFileSync('psql', ['-d', db, '-tAc', q], { encoding: 'utf8' }).trim()
const rows = (query) => sql(DB, query).split('\n').filter(Boolean)
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

/* `p118_db` là kho DEMO và nó nằm trong container, KHÁC PostgreSQL local đang
   giữ `p118_e2e_db`. Hai máy chủ cùng cổng 5432 (một qua socket, một qua TCP),
   nên gọi `psql -d p118_db` ở đây sẽ hỏi nhầm máy. Hỏi thẳng container để câu
   "kho demo có bị đụng không" đo đúng kho demo. */
const DEM_DEMO =
  "SELECT (SELECT count(*) FROM users)||'/'||(SELECT count(*) FROM workflows)" +
  "||'/'||(SELECT count(*) FROM service_approvals)||'/'||(SELECT count(*) FROM service_quotes)"
const demSoDuDemo = () =>
  execFileSync('docker', ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', 'p118_db', '-tAc', DEM_DEMO],
    { encoding: 'utf8' }).trim()

if (sql(DB, 'SELECT current_database()') !== DB) {
  console.log('DỪNG: sai database')
  process.exit(2)
}

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
const ngu = (ms) => new Promise((r) => setTimeout(r, ms))

async function taiKhoan(prefix, role) {
  const u = prefix + Math.floor(Math.random() * 1e7)
  await api('/auth/register', { method: 'POST', body: { username: u, password: PW } })
  if (role) sql(DB, `UPDATE users SET role=${q(role)} WHERE username=${q(u)}`)
  const r = await api('/auth/login', { method: 'POST', body: { username: u, password: PW } })
  return { u, token: r.json.access_token }
}

/* Chờ tới khi màn hình dừng ở một trạng thái CẦN NGƯỜI, chứ không chờ một
   trạng thái đoán trước: nếu model đi một đường khác, ta muốn thấy đúng đường
   ấy trong báo cáo chứ không muốn treo 120 giây rồi nói "timeout". */
const DUNG = new Set([
  'WAITING_PROVIDER_PROPOSAL', 'WAITING_PROVIDER_RESELECTION', 'WAITING_SERVICE_APPROVAL',
  'NEEDS_INFORMATION', 'SUCCESS', 'FAILED', 'CANCELLED', 'PLANNING_ERROR', 'VALIDATION_ERROR', 'EXECUTION_ERROR',
])
async function choDung(wid, token, giay = 150) {
  for (let i = 0; i < giay; i += 1) {
    const v = (await api(`/workflows/demo/${wid}`, { token })).json ?? {}
    if (DUNG.has(v.stage) || DUNG.has(v.status)) return v
    await ngu(1000)
  }
  throw new Error('workflow không tới điểm dừng nào trong 150 giây')
}

/** Gán tài khoản đơn vị cho ĐÚNG mã đang giữ hàng đợi, rồi quyết định. */
async function donViQuyetDinh(wid, taskId, ma, quyet) {
  const dv = await taiKhoan('dvm', 'provider')
  sql(DB, `INSERT INTO service_provider_accounts (user_id, service_provider_id)
           SELECT id, ${q(ma)} FROM users WHERE username=${q(dv.u)} ON CONFLICT DO NOTHING`)
  const t = (await api('/auth/login', { method: 'POST', body: { username: dv.u, password: PW } })).json.access_token
  return api(`/service-approvals/${wid}/${taskId}/decide`, { token: t, method: 'POST', body: quyet })
}

const maCuaBuoc = (wid, taskId) =>
  rows(`SELECT service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid AND task_id=${q(taskId)}`)[0]

const NGAY = new Date(Date.now() + 60 * 86400000).toISOString().slice(0, 10)

/**
 * Một lượt đầy đủ tới lúc đơn vị A quyết định. Trả về mọi thứ cần cho phần sau.
 * `maTuChoi` quyết định bài chính hay bài đối chứng.
 */
async function toiLucBiTuChoi(nhan, maTuChoi) {
  console.log(`\n=== ${nhan} — model thật, đơn vị từ chối ${maTuChoi} ===`)
  const kh = await taiKhoan('khm')
  const cau = `Mình muốn đặt lịch chuyển nhà ngày ${NGAY} lúc 8 giờ sáng, đi xe tải nhỏ, không cần thang máy và không cần người bốc vác.`
  console.log(`  câu khách gõ: ${cau}`)

  const bd = await api('/workflows/demo/start', { token: kh.token, method: 'POST', body: { goal: cau } })
  if (bd.status !== 202 && bd.status !== 200) throw new Error(`start trả ${bd.status}`)
  const wid = bd.json.workflow_id

  let v = await choDung(wid, kh.token)
  // Model có thể hỏi thêm một ô. Trả lời đúng một lần rồi đi tiếp — vẫn là
  // đường thật, và câu hỏi ấy chính là thứ đáng ghi lại trong báo cáo.
  if (v.status === 'NEEDS_INFORMATION') {
    console.log(`  model hỏi thêm: ${(v.question ?? v.message ?? '').slice(0, 90)}`)
    await api(`/workflows/demo/${wid}/continue`, {
      token: kh.token, method: 'POST',
      body: { fields: { move_date: NGAY, move_time: '08:00', move_vehicle: 'van', needs_elevator: false, needs_loading_support: false } },
    })
    v = await choDung(wid, kh.token)
  }

  const keHoach = rows(`SELECT task_id || ':' || tool FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`)
  console.log(`  kế hoạch model sinh: ${keHoach.join(', ')}`)
  check(`${nhan}: model dừng ở đề xuất đơn vị`, v.stage === 'WAITING_PROVIDER_PROPOSAL', v.stage)
  check(`${nhan}: kế hoạch đúng một bước chuyển nhà`, keHoach.length === 1 && keHoach[0].endsWith(':schedule_move'), keHoach.join(','))

  const dxA = v.service_proposals?.[0]
  check(`${nhan}: có đúng một đề xuất`, v.service_proposals?.length === 1)
  console.log(`  đề xuất A: ${dxA?.proposal_id?.slice(0, 8)} · ${dxA?.provider?.id} · ${dxA?.amount} ${dxA?.currency}`)

  await api(`/service-proposals/${dxA.proposal_id}/confirm`, { token: kh.token, method: 'POST', body: { decision: 'confirm' } })
  v = await choDung(wid, kh.token)
  check(`${nhan}: đồng ý xong thì tới hàng đợi đơn vị`, v.stage === 'WAITING_SERVICE_APPROVAL', v.stage)

  const maA = maCuaBuoc(wid, 'T1')
  check(`${nhan}: hàng đợi thuộc đúng đơn vị được đề xuất`, maA === dxA.provider.id, `${maA} vs ${dxA.provider.id}`)

  const r = await donViQuyetDinh(wid, 'T1', maA, {
    decision: 'reject', reject_code: maTuChoi,
    reject_reason: maTuChoi === 'SERVICE_UNAVAILABLE' ? 'Bên mình không nhận tuyến này.' : 'Yêu cầu thiếu thông tin toà nhà.',
  })
  check(`${nhan}: đơn vị A từ chối được`, r.status === 200, `http ${r.status}`)
  await ngu(3000)
  return { kh, wid, maA, dxA, v: (await api(`/workflows/demo/${wid}`, { token: kh.token })).json }
}

const demBuoc = (wid) => Number(rows(`SELECT count(*) FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid`)[0])
const p118Truoc = demSoDuDemo()

// ─────────────────────────────────────────── BÀI CHÍNH: SERVICE_UNAVAILABLE
{
  const { kh, wid, maA, v } = await toiLucBiTuChoi('CHÍNH', 'SERVICE_UNAVAILABLE')
  check('CHÍNH: khách thấy đúng bước chọn lại', v.stage === 'WAITING_PROVIDER_RESELECTION', v.stage)
  check('CHÍNH: khách đọc được lý do thật', (v.provider_rejection?.sanitized_reason ?? '').includes('không nhận tuyến'))
  check('CHÍNH: hệ thống mời tìm đơn vị khác', v.provider_rejection?.can_request_another_provider === true)
  check('CHÍNH: chưa tự mở lần thử nào', demBuoc(wid) === 1)

  const bam = await api(`/service-proposals/workflows/${wid}/request-another-provider`, {
    token: kh.token, method: 'POST', body: { task_id: 'T1' },
  })
  check('CHÍNH: bấm tìm đơn vị khác được nhận', bam.status === 200, `http ${bam.status}`)
  const v2 = await choDung(wid, kh.token)
  const dxB = v2.service_proposals?.[0]
  check('CHÍNH: có đề xuất mới', v2.stage === 'WAITING_PROVIDER_PROPOSAL' && !!dxB, v2.stage)
  check('CHÍNH: đơn vị B khác đơn vị đã từ chối', dxB?.provider?.id !== maA, `${maA} → ${dxB?.provider?.id}`)
  check('CHÍNH: đúng hai lần thử, không hơn', demBuoc(wid) === 2, String(demBuoc(wid)))

  await api(`/service-proposals/${dxB.proposal_id}/confirm`, { token: kh.token, method: 'POST', body: { decision: 'confirm' } })
  await choDung(wid, kh.token)
  const maB = maCuaBuoc(wid, 'T1R2')
  check('CHÍNH: hàng đợi mới thuộc đơn vị B', maB === dxB.provider.id, `${maB}`)
  const ok = await donViQuyetDinh(wid, 'T1R2', maB, { decision: 'approve' })
  check('CHÍNH: đơn vị B duyệt được', ok.status === 200, `http ${ok.status}`)
  const cuoi = await choDung(wid, kh.token)
  check('CHÍNH: yêu cầu hoàn tất', cuoi.status === 'SUCCESS', cuoi.status)

  const buoc = rows(`SELECT task_id||'='||status FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`)
  const duyet = rows(`SELECT task_id||'='||status||'/'||service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`)
  const bg = rows(`SELECT count(*)||' báo giá' FROM service_quotes WHERE workflow_id=${q(wid)}::uuid`)
  const dx = rows(`SELECT count(*)||' đề xuất' FROM service_provider_proposals WHERE workflow_id=${q(wid)}::uuid`)
  check('CHÍNH: mỗi lần thử đúng một dòng duyệt', duyet.length === 2, duyet.join(' '))
  check('CHÍNH: đúng hai đề xuất', dx[0] === '2 đề xuất', dx[0])
  console.log(`\n  workflow ${wid}`)
  console.log(`  bước   : ${buoc.join(', ')}`)
  console.log(`  duyệt  : ${duyet.join(', ')}`)
  console.log(`  chứng từ: ${bg[0]}, ${dx[0]}`)
}

// ──────────────────────────────────── ĐỐI CHỨNG: INVALID_REQUEST
{
  const { kh, wid, v } = await toiLucBiTuChoi('ĐỐI CHỨNG', 'INVALID_REQUEST')
  check('ĐỐI CHỨNG: KHÔNG mời tìm đơn vị khác', v.provider_rejection?.can_request_another_provider !== true,
        String(v.provider_rejection?.can_request_another_provider))
  check('ĐỐI CHỨNG: không tự đổi đơn vị', demBuoc(wid) === 1, String(demBuoc(wid)))
  check('ĐỐI CHỨNG: không có T1R2', rows(`SELECT count(*) FROM workflow_tasks WHERE workflow_id=${q(wid)}::uuid AND task_id='T1R2'`)[0] === '0')

  // Gọi thẳng endpoint, bỏ qua giao diện: luật phải đứng ở tầng dưới.
  const bam = await api(`/service-proposals/workflows/${wid}/request-another-provider`, {
    token: kh.token, method: 'POST', body: { task_id: 'T1' },
  })
  check('ĐỐI CHỨNG: endpoint cũng từ chối mở lần thử', bam.status !== 200, `http ${bam.status}`)
  check('ĐỐI CHỨNG: sau khi gọi thẳng vẫn đúng một bước', demBuoc(wid) === 1, String(demBuoc(wid)))
  console.log(`\n  workflow ${wid} · vẫn 1 bước, 1 dòng duyệt REJECTED/INVALID_REQUEST`)
}

const p118Sau = demSoDuDemo()
check('p118_db không bị đụng', p118Truoc === p118Sau, `${p118Truoc} → ${p118Sau}`)

console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
process.exit(loi.length ? 1 : 0)
