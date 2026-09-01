/**
 * Luồng HUỶ, đo trên browser thật — từ nút của khách tới bản ghi ở phía đơn vị.
 *
 * Bốn thứ chỉ browser mới trả lời được, và cả bốn từng sai:
 *   ① thẻ kết quả có hiện cho dịch vụ KHÔNG phải tham quan không
 *   ② nút "Huỷ lịch" có GỬI gì không, hay chỉ hiện một dòng chữ
 *   ③ trang Lịch sử còn tự quyết được gì không (phải là màn hình ĐỌC)
 *   ④ đơn vị duyệt xong thì chỗ có thật sự được trả về kho không
 *
 * Chạy trên stack RIÊNG (`p118ui`, database `p118_ui_db`) — không chạm dữ liệu
 * demo. Xem `docker-compose.uitest.yml` trong scratchpad.
 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const PW = 'Passw0rd!123'
const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:5199'
const API = process.env.E2E_API ?? 'http://127.0.0.1:8100'
const CT = process.env.E2E_PG ?? 'p118ui_postgres'
const DB = process.env.E2E_DB ?? 'p118_ui_db'

const sql = q => execFileSync('docker', ['exec', CT, 'psql', '-U', 'p118', '-d', DB, '-tAc', q], { encoding: 'utf8' })
  .trim().split('\n').filter(Boolean)
const api = async (path, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}
const wait = async (f, ms = 180000) => {
  const t = Date.now()
  while (Date.now() - t < ms) { if (await f()) return true; await new Promise(r => setTimeout(r, 2000)) }
  return false
}

let pass = 0, fail = 0
const check = (name, ok, detail = '') => {
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${name}${ok || !detail ? '' : ` — ${detail}`}`)
  ok ? pass++ : fail++
}

const browser = await chromium.launch()
const page = await (await browser.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
const jsErrors = []
page.on('pageerror', e => jsErrors.push(String(e)))

const U = 'huy' + Math.floor(Math.random() * 1e6)
await page.goto(`${BASE}/register`)
await page.fill('#reg-username', U)
await page.fill('#reg-email', `${U}@e.test`)
await page.fill('#reg-password', PW)
await page.fill('#reg-confirm', PW)
await page.click('button[type=submit]')
await page.waitForURL('**/workspace', { timeout: 30000 })

const uid = sql(`SELECT id FROM users WHERE username='${U}'`)[0]
// Database này dựng mới nên `residents` rỗng — `seed.sql` không tạo cư dân.
// Tạo một hồ sơ riêng cho lượt chạy này thay vì mượn của ai đó.
const rid = 'RES-E2E-' + Math.floor(Math.random() * 100000)
sql(`INSERT INTO residents (resident_id, full_name, apartment_code, residential_area)
     VALUES ('${rid}','Nguyen Van E2E','A${Math.floor(Math.random() * 9000 + 1000)}','Vinhomes Ocean Park')
     ON CONFLICT DO NOTHING`)
sql(`INSERT INTO user_resident_links (user_id,resident_id,verification_status,verified_at)
     VALUES ('${uid}','${rid}','VERIFIED',now())
     ON CONFLICT (user_id) DO UPDATE SET verification_status='VERIFIED',verified_at=now()`)
await page.reload()
await page.waitForTimeout(1500)

const token = (await api('/api/v1/auth/login', { method: 'POST', body: { username: U, password: PW } })).json.access_token
const plate = '51K-' + Math.floor(Math.random() * 90000 + 10000)
await page.fill('textarea', `Giữ chỗ đỗ xe Khu B ngày 2029-09-27 cho xe máy biển số ${plate}`)
await page.keyboard.press('Enter')

const wfOf = () => sql(`SELECT w.workflow_id FROM workflows w JOIN users u ON u.id=w.owner_user_id
                        WHERE u.username='${U}' AND w.task_plan::text<>'null'
                        ORDER BY w.created_at DESC LIMIT 1`)[0]
const gotQueue = await wait(async () => {
  const w = wfOf()
  return w && sql(`SELECT count(*) FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)[0] !== '0'
})
const wf = wfOf()
check('0. tạo được yêu cầu và nó tới hàng đợi đơn vị', Boolean(wf && gotQueue),
  wf ? '' : (await page.textContent('body')).replace(/\s+/g, ' ').slice(0, 200))
if (!wf) { await browser.close(); process.exit(1) }

// Tài khoản ĐƠN VỊ. Quyết định đi qua chính route người duyệt bấm — không
// UPDATE thẳng database, vì như thế là bỏ qua đúng đoạn đang cần kiểm.
const P = 'dv' + Math.floor(Math.random() * 1e6)
await api('/api/v1/auth/register', { method: 'POST', body: { username: P, password: PW } })
sql(`UPDATE users SET role='provider' WHERE username='${P}'`)
const ptok = (await api('/api/v1/auth/login', { method: 'POST', body: { username: P, password: PW } })).json.access_token

const duyet = async (taskId) =>
  api(`/api/v1/service-approvals/${wf}/${taskId}/decide`, { token: ptok, method: 'POST', body: { decision: 'approve' } })

for (const t of sql(`SELECT task_id FROM service_approvals WHERE workflow_id='${wf}' AND status='AWAITING' AND kind='TASK'`)) {
  const r = await duyet(t)
  if (r.status !== 200) console.log(`    (duyệt ${t} → ${r.status} ${JSON.stringify(r.json).slice(0, 140)})`)
}
const booked = await wait(async () =>
  sql(`SELECT count(*) FROM workflow_tasks WHERE workflow_id='${wf}' AND tool='book_parking' AND status='SUCCESS'`)[0] !== '0')
check('1. chỗ đỗ được giữ thật sau khi đơn vị duyệt', booked,
  sql(`SELECT task_id||' '||tool||' '||status FROM workflow_tasks WHERE workflow_id='${wf}'`).join(' | '))

const bookingId = sql(`SELECT result_data->>'booking_id' FROM workflow_tasks
                       WHERE workflow_id='${wf}' AND tool='book_parking' AND status='SUCCESS'`)[0]
check('2. có mã đặt chỗ thật', Boolean(bookingId), String(bookingId))

// Khách xác nhận thanh toán. Thẻ kết quả — và hai nút trên nó — chỉ hiện khi
// yêu cầu đã HOÀN TẤT; chỗ đỗ xe còn một bước trả tiền sau khi đơn vị duyệt.
const traTien = await api(`/api/v1/workflows/demo/${wf}/payment-decision`, {
  token, method: 'POST', body: { decision: 'approve' },
})
const xong = await wait(async () => sql(`SELECT status FROM workflows WHERE workflow_id='${wf}'`)[0] === 'SUCCESS', 90000)
check('2b. trả tiền xong thì yêu cầu hoàn tất', xong,
  `http=${traTien.status} status=${sql(`SELECT status FROM workflows WHERE workflow_id='${wf}'`)[0]}`)

// ── Trang chi tiết: thẻ kết quả + hai nút ──────────────────────────────────
await page.goto(`${BASE}/workflow/${wf}`)
await page.waitForTimeout(3000)
const body = () => page.textContent('body').then(t => t.replace(/\s+/g, ' '))

const t1 = await body()
check('3. thẻ kết quả hiện cho chỗ đỗ xe (không chỉ tham quan)',
  /Huỷ lịch/.test(t1) && /Đổi lịch/.test(t1), t1.slice(0, 220))
check('4. thẻ KHÔNG gọi tên một dịch vụ khác', !/Lịch tham quan|Trước buổi tham quan/.test(t1))
check('5. Lịch sử không còn ô chat tự do', (await page.locator('textarea').count()) === 0)
check('6. Lịch sử không còn nút quyết định thanh toán',
  !/Xác nhận thanh toán/.test(t1) && !/Dừng yêu cầu này/.test(t1))

// ── Bấm Huỷ lịch ───────────────────────────────────────────────────────────
page.on('dialog', d => d.accept())
await page.click('button:has-text("Huỷ lịch")')
await page.waitForTimeout(2500)
const t2 = await body()
const requests = sql(`SELECT task_id||' '||status||' '||kind||' '||service_label
                      FROM service_approvals WHERE workflow_id='${wf}' AND kind='REQUEST'`)
check('7. nút Huỷ lịch GỬI một hồ sơ tới đơn vị', requests.length === 1, requests.join(' | '))
check('8. hồ sơ mang nhãn đọc được cho người duyệt', /Xin huỷ/.test(requests[0] ?? ''), requests[0])
check('9. màn hình nói lại bằng câu backend viết', /chuyển yêu cầu tới đơn vị/i.test(t2), t2.slice(-260))
check('10. chỗ đỗ CHƯA bị đụng khi đơn vị chưa quyết',
  sql(`SELECT status FROM parking_bookings WHERE booking_id='${bookingId}'`)[0] === 'ACTIVE')

// ── Đơn vị duyệt hồ sơ ─────────────────────────────────────────────────────
const reqId = requests[0].split(' ')[0]
const quyet = await duyet(reqId)
check('10b. đơn vị duyệt được hồ sơ liên hệ qua đúng route của họ', quyet.status === 200,
  `${quyet.status} ${JSON.stringify(quyet.json).slice(0, 160)}`)
const released = await wait(async () =>
  sql(`SELECT status FROM parking_bookings WHERE booking_id='${bookingId}'`)[0] === 'CANCELLED', 60000)
check('11. đơn vị đồng ý → chỗ được trả về kho', released,
  sql(`SELECT status FROM parking_bookings WHERE booking_id='${bookingId}'`).join())
const cancelTask = sql(`SELECT task_id||' '||status||' '||provider_submission_status
                        FROM workflow_tasks WHERE workflow_id='${wf}' AND tool='cancel_parking'`)
check('12. bước huỷ để lại bằng chứng gửi đi', /SUCCESS ACKNOWLEDGED/.test(cancelTask[0] ?? ''), cancelTask.join(' | '))
check('13. huỷ không dựng thêm bước nào khác',
  sql(`SELECT count(*) FROM workflow_tasks WHERE workflow_id='${wf}' AND tool='cancel_parking'`)[0] === '1')

check('14. không có lỗi JS nào trên trang', jsErrors.length === 0, jsErrors.join(' | '))

console.log(`\n${pass} ok, ${fail} FAIL`)
await browser.close()
process.exit(fail ? 1 : 0)
