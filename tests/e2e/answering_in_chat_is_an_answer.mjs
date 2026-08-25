/**
 * Gõ câu trả lời vào ô chat phải TRẢ LỜI, không mở yêu cầu mới.
 *
 * Đo được, phiên e88a96e1 trên stack demo: năm lượt gõ tạo NĂM workflow trong
 * cùng một phiên, ~112 giây gọi model để nhập ba ô. 13/14 hồ sơ câu hỏi trong
 * dữ liệu ghi được đều `resolved=false` — đúng một cái được giải quyết, và đó
 * là lượt đi qua BIỂU MẪU.
 *
 * Gốc: nhánh trả lời trong `execute()` bị chặn bởi `mode === 'journey'`, mà
 * `mode` chỉ thành `'journey'` khi kế hoạch có bước. Workflow đang chờ trả lời
 * thì chưa có bước nào.
 *
 * Chạy trên BẢN BUILD PRODUCTION, không phải dev server: lỗi build và lỗi chỉ
 * hiện ra sau khi minify đều không thấy được ở dev.
 */
import { chromium } from 'playwright'

const APP = process.env.E2E_BASE ?? 'http://127.0.0.1:5299'
const API = process.env.E2E_API ?? 'http://127.0.0.1:8000/api/v1'
const U = `chatanswer_${Date.now()}`
const P = 'Matkhau!123'
const KEY = 'p118.access_token'

const call = async (path, opt = {}) => {
  const r = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) },
    ...opt,
  })
  const t = await r.text()
  try { return { status: r.status, body: JSON.parse(t) } } catch { return { status: r.status, body: t } }
}

let ma = 0
const kiem = (dieu, ten) => { console.log(`${dieu ? '  PASS' : '  FAIL'}  ${ten}`); if (!dieu) ma = 1 }

await call('/auth/register', { method: 'POST', body: JSON.stringify({ username: U, password: P }) })
const dn = await call('/auth/login', { method: 'POST', body: JSON.stringify({ username: U, password: P }) })
const token = dn.body.access_token
if (!token) { console.error('  khong dang nhap duoc:', dn); process.exit(1) }
const H = { Authorization: `Bearer ${token}` }

const dem = async () => {
  const r = await call('/workflows/demo', { headers: H })
  const b = r.body
  return Array.isArray(b) ? b.length : (b?.items?.length ?? b?.workflows?.length ?? -1)
}

const b = await chromium.launch()
const pg = await b.newPage()
const loi = []
pg.on('pageerror', (e) => loi.push(String(e).slice(0, 200)))
await pg.goto(APP + '/', { waitUntil: 'domcontentloaded' })
await pg.evaluate(([k, t]) => sessionStorage.setItem(k, t), [KEY, token])
//  không bao giờ tới: luồng SSE thông báo giữ kết nối mở.
await pg.goto(APP + '/workspace', { waitUntil: 'domcontentloaded' })
await pg.waitForSelector('textarea', { timeout: 15000 })

const go = async (chu) => {
  const o = pg.locator('textarea').first()
  await o.click()
  await o.fill(chu)
  await o.press('Enter')
  await pg.waitForTimeout(2500)
}

await go('đặt lịch tham quan')
await pg.waitForTimeout(4000)
const sauLuot1 = await dem()
kiem(sauLuot1 === 1, `lượt đầu tạo đúng 1 yêu cầu (thấy ${sauLuot1})`)

const coThe = await pg.locator('text=Cần thêm thông tin').count()
kiem(coThe > 0, 'thẻ "Cần thêm thông tin" hiện ra')

await go('Vinhomes Ocean Park')
await pg.waitForTimeout(3000)
const sauLuot2 = await dem()
kiem(sauLuot2 === 1, `trả lời trong ô chat KHÔNG mở yêu cầu mới (thấy ${sauLuot2} yêu cầu)`)

kiem(loi.length === 0, `không có lỗi runtime (${loi.slice(0, 2).join(' | ')})`)

await b.close()
console.log(`\nUSER=${U}`)
process.exit(ma)
