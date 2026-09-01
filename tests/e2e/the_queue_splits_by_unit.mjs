/**
 * Browser E2E: hàng đợi duyệt chia theo ĐƠN VỊ, trong từng loại dịch vụ.
 *
 * Vì sao cần: một tài khoản có thể được gắn nhiều đơn vị
 * (`service_provider_accounts` khoá chính là cặp `(user_id, service_provider_id)`),
 * và khi đó "Chuyển nhà" là hàng đợi của mấy đội khác nhau nằm lẫn vào nhau.
 * Không chia thì người duyệt bấm Duyệt mà không biết mình nhân danh đội nào.
 *
 * Bài kiểm CẢ HAI phía của luật:
 *   - tài khoản kiêm nhiệm  → có hàng chip, lọc đúng, thẻ ghi tên đơn vị;
 *   - tài khoản một đơn vị  → KHÔNG có hàng chip (nó không nói thêm gì) và
 *     `total` chỉ đếm phần của đơn vị ấy.
 *
 * Chạy trên stack Docker (`p118_db`) + vite dev cổng 5273. Bài này CHỈ ĐỌC —
 * không bấm Duyệt/Từ chối, nên nó không đổi trạng thái việc nào.
 *
 * Cần sẵn: `dv_tatca` (gắn mọi đơn vị) và `dv_chuyennha_a` (chỉ MOV-01), cùng
 * vài việc đang chờ của ít nhất hai đơn vị chuyển nhà.
 */
import { chromium } from 'playwright'
const APP='http://127.0.0.1:5273', PW='Passw0rd!123'
const loi=[]; const check=(t,ok,ct='')=>{console.log(`  ${ok?'✓':'✗'} ${t}${ct?` — ${ct}`:''}`); if(!ok) loi.push(t)}
const b=await chromium.launch(); const page=await (await b.newContext({viewport:{width:1440,height:1000}})).newPage()
const jsErr=[]; page.on('pageerror',e=>jsErr.push(e.message))

async function login(u){ await page.goto(APP+'/login'); await page.fill('#login-username',u); await page.fill('#login-password',PW); await page.click('button[type=submit]'); await page.waitForTimeout(3000) }

console.log('\n=== Tài khoản kiêm nhiệm 9 đơn vị ===')
await login('dv_tatca')
await page.goto(APP+'/review'); await page.waitForTimeout(3000)
await page.getByRole('tab',{name:/Dịch vụ/}).click().catch(()=>{})
await page.locator('button:has-text("Dịch vụ")').first().click().catch(()=>{})
await page.waitForTimeout(2500)
const body=await page.textContent('body')
check('vào được hàng đợi dịch vụ', body.includes('Chuyển nhà'), '')

const chipDonVi = page.locator('[role="tablist"][aria-label="Đơn vị cung cấp"] button')
const n = await chipDonVi.count()
const nhan = []
for (let i=0;i<n;i++) nhan.push((await chipDonVi.nth(i).textContent()).trim())
console.log('  chip đơn vị:', nhan.join(' | '))
check('có hàng chip đơn vị', n >= 4, `${n} chip`)
check('chip đầu là Tất cả', nhan[0]?.startsWith('Tất cả'), nhan[0])
check('ba đơn vị chuyển nhà đều có chip', ['Minh Phát','Đại Tín','An Khang'].every(x=>nhan.some(l=>l.includes(x))), nhan.slice(1).join(','))

// Tất cả: mỗi thẻ ghi tên đơn vị
const the = page.locator('ul > li')
const soThe = await the.count()
check('Tất cả hiện đủ việc', soThe === 4, `${soThe} thẻ`)
const t0 = await the.first().textContent()
check('thẻ ghi tên đơn vị khi xem lẫn', /Minh Phát|Đại Tín|An Khang/.test(t0), t0.slice(0,60).replace(/\s+/g,' '))

// Lọc về An Khang
const chipAn = chipDonVi.filter({hasText:'An Khang'})
const soAn = Number((await chipAn.textContent()).match(/\((\d+)\)/)?.[1])
await chipAn.click(); await page.waitForTimeout(800)
const sauLoc = await page.locator('ul > li').count()
check('lọc theo đơn vị đúng số việc', sauLoc === soAn, `chip nói ${soAn}, hiện ${sauLoc}`)
const t1 = await page.locator('ul > li').first().textContent()
check('đã lọc thì không lặp lại tên đơn vị trên thẻ', !/Minh Phát|Đại Tín/.test(t1))

// Tổng đã đúng phạm vi
const tong = Number(body.match(/(\d+)\s+mục/)?.[1])
check('tổng đếm đúng phần của tài khoản này', tong === 4, `${tong} mục`)

console.log('\n=== Provider một đơn vị: không thấy hàng chia ===')
await login('dv_chuyennha_a')
await page.goto(APP+'/review'); await page.waitForTimeout(3000)
await page.locator('button:has-text("Dịch vụ")').first().click().catch(()=>{})
await page.waitForTimeout(2000)
check('không dựng hàng chip cho tài khoản một đơn vị',
  (await page.locator('[role="tablist"][aria-label="Đơn vị cung cấp"]').count()) === 0)
const tong2 = Number((await page.textContent('body')).match(/(\d+)\s+mục/)?.[1])
check('tổng của đơn vị này chỉ đếm việc của nó', tong2 === 1, `${tong2} mục`)

console.log('\nlỗi JS trên trang:', jsErr.length?jsErr.join(' | '):'không')
console.log(loi.length?`\nHỎNG ${loi.length}: ${loi.join(' · ')}`:'\nTẤT CẢ ĐẠT')
await b.close(); process.exit(loi.length?1:0)
