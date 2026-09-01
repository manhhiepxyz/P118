/**
 * AUDIT bằng TRÌNH DUYỆT — năm khẳng định của buổi demo.
 *
 * Khác `audit_ownership_and_totals.mjs` (gọi thẳng API): bài này đo THỨ NGƯỜI
 * XEM NHÌN THẤY. Hai bài cần cả hai — API đúng mà màn hình vẽ sai thì buổi demo
 * vẫn hỏng, và màn hình đúng mà API lộ thì hệ thống vẫn lộ.
 *
 * Chạy trên stack Docker (`p118_db`) + vite dev 5273, cờ SERVICE_PROVIDER_MATCHING=1.
 * Bài này CHỈ ĐỌC — không bấm Duyệt/Từ chối/Xác nhận.
 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const APP = process.env.P118_APP ?? 'http://127.0.0.1:5273'
const PW = 'Passw0rd!123'
const psql = (q) => execFileSync('docker', ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', 'p118_db', '-tAc', q], { encoding: 'utf8' }).trim()

const loi = []
const check = (t, ok, ct = '') => { console.log(`  ${ok ? '✓' : '✗'} ${t}${ct ? ` — ${ct}` : ''}`); if (!ok) loi.push(t) }
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 1100 } })
const page = await ctx.newPage()
const jsErr = []
page.on('pageerror', (e) => jsErr.push(e.message))

async function login(u) {
  await ctx.clearCookies()
  await page.goto(APP + '/login')
  await page.evaluate(() => { sessionStorage.clear(); localStorage.clear() })
  await page.goto(APP + '/login')
  await page.fill('#login-username', u); await page.fill('#login-password', PW)
  await page.click('button[type=submit]'); await page.waitForTimeout(3000)
}
async function moHangDoi() {
  await page.goto(APP + '/review'); await page.waitForTimeout(3000)
  await page.locator('button:has-text("Dịch vụ")').first().click().catch(() => {})
  await page.waitForTimeout(2500)
}
const chip = () => page.locator('[role="tablist"][aria-label="Đơn vị cung cấp"] button')

// ─────────────────────────────────── 1. Đơn vị MOV-01
console.log('\n1 — Đơn vị MOV-01 (dv_chuyennha_a)')
await login('dv_chuyennha_a')
await moHangDoi()
const t1 = await page.textContent('body')
const nMOV1 = Number(psql("SELECT count(*) FROM service_approvals WHERE status='AWAITING' AND service_provider_id='MOV-01'"))
check('không có hàng chip (chỉ giữ một đơn vị)', (await chip().count()) === 0)
check('không đọc được tên đơn vị khác', !/Vận tải Đại Tín|Dịch vụ An Khang/.test(t1))
check('số mục khớp đúng phần của MOV-01', Number(t1.match(/(\d+)\s+mục/)?.[1]) === nMOV1, `màn hình ${t1.match(/(\d+)\s+mục/)?.[1]}, database ${nMOV1}`)
check('số thẻ khớp số mục', (await page.locator('ul > li').count()) === nMOV1)

// ─────────────────────────────────── 2. Đơn vị MOV-02
console.log('\n2 — Đơn vị MOV-02 (dv_chuyennha_b)')
await login('dv_chuyennha_b')
await moHangDoi()
const t2 = await page.textContent('body')
const nMOV2 = Number(psql("SELECT count(*) FROM service_approvals WHERE status='AWAITING' AND service_provider_id='MOV-02'"))
check('không có hàng chip', (await chip().count()) === 0)
check('không đọc được tên đơn vị khác', !/Chuyển nhà Minh Phát|Dịch vụ An Khang/.test(t2))
check('số mục khớp đúng phần của MOV-02', Number(t2.match(/(\d+)\s+mục/)?.[1]) === nMOV2, `màn hình ${t2.match(/(\d+)\s+mục/)?.[1]}, database ${nMOV2}`)

// ─────────────────────────────────── 3. Tài khoản kiêm nhiệm
console.log('\n3 — Tài khoản kiêm nhiệm (dv_tatca)')
await login('dv_tatca')
await moHangDoi()
const nChip = await chip().count()
const nhan = []
for (let i = 0; i < nChip; i++) nhan.push((await chip().nth(i).textContent()).trim())
console.log('  chip:', nhan.join(' | '))
check('có hàng chip đơn vị', nChip >= 3)
// Tab LOẠI DỊCH VỤ: mỗi loại khác vẫn là một hàng đợi legacy, không có gì để chọn.
const tabLoai = page.locator('[role="tablist"][aria-label="Loại dịch vụ"] button')
const loai = []
for (let i = 0; i < (await tabLoai.count()); i++) loai.push((await tabLoai.nth(i).textContent()).trim())
console.log('  tab loại dịch vụ:', loai.length ? loai.join(' | ') : '(chỉ một loại nên tự ẩn)')
check('không có nút "chọn đơn vị" nào trong hàng đợi', (await page.locator('text=/Chọn đơn vị|Đổi đơn vị|Tìm đơn vị khác/').count()) === 0)

// ─────────────────────────────────── 4. Admin
console.log('\n4 — Admin')
// Tài khoản admin tạo qua API rồi nâng vai — form đăng ký trên UI không có
// đường chọn vai, và dựng nó bằng cách bấm form là đo cái khác.
const adm = 'audit_ui_admin' + Math.floor(Math.random() * 1e6)
await fetch(`${process.env.P118_API ?? 'http://127.0.0.1:8000'}/api/v1/auth/register`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ username: adm, password: PW }),
})
psql(`UPDATE users SET role='admin' WHERE username='${adm.toLowerCase()}'`)
await login(adm.toLowerCase())
await page.goto(APP + '/review'); await page.waitForTimeout(3000)
check('admin KHÔNG vào được /review', !page.url().includes('/review'), page.url().replace(APP, ''))
await page.goto(APP + '/admin/workflows'); await page.waitForTimeout(3500)
const ta = await page.textContent('body')
check('admin xem được tổng hợp qua /admin', page.url().includes('/admin'), page.url().replace(APP, ''))
const nutQuyet = await page.locator('button:has-text("Duyệt"), button:has-text("Từ chối")').count()
check('màn admin KHÔNG có nút quyết định', nutQuyet === 0, `${nutQuyet} nút`)
check('admin đọc được số liệu, không phải màn trống', ta.length > 400)

console.log('\nlỗi JS trên trang:', jsErr.length ? jsErr.join(' | ') : 'không')
console.log(loi.length ? `\nHỎNG ${loi.length}:\n  - ${loi.join('\n  - ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
