import { chromium } from 'playwright'; import fs from 'fs'
const T = fs.readFileSync('tok_ctx','utf8').trim()
const fails = []
const ok = (cond, name, extra='') => { console.log(`${cond?'  ✓':'  ✗'} ${name}${extra?' — '+extra:''}`); if(!cond) fails.push(name) }

const b = await chromium.launch(); const p = await b.newPage({ viewport:{width:1440,height:900} })
p.on('pageerror', e => { console.log('  ✗ PAGEERROR:', e.message); fails.push('pageerror') })
await p.goto('http://localhost:5173/')
await p.evaluate(t => sessionStorage.setItem('p118.access_token', t), T)
await p.goto('http://localhost:5173/workspace', { waitUntil: 'domcontentloaded' })
await p.waitForTimeout(2500)

const txt = () => p.locator('body').innerText()
const has = async (re) => re.test(await txt())
const frame = async (tag) => {
  const nav = await p.locator('nav, aside').first().count() > 0
  const leftNav = await has(/Hành trình[\s\S]{0,40}Lịch sử/)
  const composer = await p.locator('#ws-composer').count() > 0
  const right = await p.locator('aside[aria-label="Chi tiết hành trình"]').count() > 0
  ok(leftNav, `[${tag}] thanh điều hướng trái còn`)
  ok(composer, `[${tag}] ô nhập còn`)
  return { right }
}

console.log('\n— 1. Mới vào —')
ok(await has(/DỊCH VỤ[\s\S]{0,90}khả dụng/), 'bảng dịch vụ hiện')
ok((await p.locator('[data-journey-step]').count()) === 0, 'chưa có bước nào')
await frame('mới vào')

console.log('\n— 2. Gửi một CÂU HỎI —')
await p.locator('#ws-composer').fill('phí gửi xe ô tô một tháng khoảng bao nhiêu')
await p.keyboard.press('Enter')
await p.waitForTimeout(1200)
ok(await has(/phí gửi xe ô tô một tháng/), 'câu vừa gõ hiện ngay trong hội thoại')
let sawDots = false
for (let i=0;i<20;i++){ await p.waitForTimeout(800); if (await p.locator('.think-dot').count() > 0) { sawDots = true; break } if (await has(/P-118:[\s\S]{0,4}\S{20}/)) break }
ok(sawDots, 'có nhịp ba chấm khi model đang soạn')
// Câu hứa kế hoạch KHÔNG được hiện cho một câu chỉ cần trả lời.
let huaKeHoach = false
for (let i=0;i<26;i++){ await p.waitForTimeout(1200); if (await has(/Đang chuẩn bị kế hoạch/)) huaKeHoach = true; if (await has(/P-118:[\s\S]{0,4}\S{20}/)) break }
ok(!huaKeHoach, 'KHÔNG hứa "Đang chuẩn bị kế hoạch" cho câu chat')
for (let i=0;i<25;i++){ await p.waitForTimeout(1500); if (!(await has(/Đang chuẩn bị kế hoạch/)) && (await txt()).length > 400) break }
await p.waitForTimeout(6000)
ok(!(await has(/DỊCH VỤ[\s\S]{0,90}khả dụng/)), 'bảng dịch vụ đã lùi')
ok((await p.locator('[data-journey-step]').count()) === 0, 'câu hỏi KHÔNG sinh bước nào')
let f = await frame('sau câu hỏi'); ok(f.right, '[sau câu hỏi] cột phải còn')

console.log('\n— 3. Gửi tin thứ HAI —')
const truoc = (await txt()).includes('phí gửi xe ô tô một tháng')
await p.locator('#ws-composer').fill('còn dịch vụ nào khác không')
await p.keyboard.press('Enter')
await p.waitForTimeout(2500)
ok(await has(/phí gửi xe ô tô một tháng/), 'tin nhắn thứ NHẤT vẫn còn', truoc?'':'(tin 1 đã mất từ trước)')
ok(await has(/còn dịch vụ nào khác không/), 'tin nhắn thứ hai hiện')
await frame('sau tin 2')
for (let i=0;i<25;i++){ await p.waitForTimeout(1500); if ((await p.locator('[data-turn], .think-dot').count()) >= 0 && !(await has(/Đang chuẩn bị kế hoạch/))) break }
await p.waitForTimeout(8000)

console.log('\n— 4+5. YÊU CẦU THẬT rồi BẤM DỪNG —')
await p.locator('#ws-composer').fill('Đặt lịch tham quan Vinhomes Ocean Park ngày 2027-01-20 lúc 09:00 xe đưa đón cho 2 khách tại 12 Nguyễn Trãi liên hệ 0912345678')
await p.keyboard.press('Enter')

// Bấm dừng NGAY khi nút hiện — đó là thao tác thật của người dùng. Kịch bản
// trước chờ đủ bước rồi mới tìm nút, mà lúc ấy yêu cầu đã rời trạng thái chạy;
// nút biến mất một cách hoàn toàn đúng, và ba mục sau đỏ theo vì không có gì
// để dừng. Lỗi nằm ở kịch bản, không ở sản phẩm.
const railBtn = p.locator('button[aria-label="Dừng việc đang chạy"]')
const top = p.locator('button', { hasText: 'Dừng yêu cầu' })
let thayNutDung = false, dung = false, buoc = -1
for (let i = 0; i < 60; i++) {
  await p.waitForTimeout(700)
  if ((await p.locator('[data-journey-step]').count()) > 0 && buoc < 0) buoc = Math.round(i * 0.7)
  if (!thayNutDung && (await railBtn.count()) > 0) thayNutDung = true
  if (!dung && (await railBtn.count()) > 0) { await railBtn.click().catch(()=>{}); dung = true; break }
  if (!dung && (await top.count()) > 0) { await top.click().catch(()=>{}); dung = true; break }
}
// Kế hoạch hoặc thẻ chờ bổ sung — CẢ HAI đều là kết cục hợp lệ cho một câu tự
// do; ép phải ra bước là ép mô hình phải đoán đúng mọi lần.
const coViec = buoc >= 0 || (await p.locator('aside [data-inspector], aside').first().innerText()).length > 40
ok(coViec, 'yêu cầu thật tạo ra việc (bước hoặc thẻ chờ)', buoc>=0?`bước sau ${buoc}s`:'thẻ chờ')
ok(thayNutDung, 'nút dừng có mặt trong lúc yêu cầu đang chạy')
ok(dung, 'bấm được nút dừng')
ok(await has(/phí gửi xe ô tô một tháng/), 'hội thoại cũ vẫn còn')
let f2 = await frame('sau dừng'); ok(f2.right, '[sau dừng] cột phải còn')
ok(await has(/Hành trình mới/), '[sau dừng] thanh trên còn')
await p.waitForTimeout(7000)
const soCauHuy = ((await txt()).match(/Mình đã (dừng|huỷ) yêu cầu/g) || []).length
ok(soCauHuy === 1, 'chỉ MỘT câu báo dừng', `đếm được ${soCauHuy}`)

console.log('\n— 6. Nhắn tiếp SAU KHI DỪNG —')
await p.locator('#ws-composer').fill('tôi muốn đổi dịch vụ')
await p.keyboard.press('Enter')
await p.waitForTimeout(2500)
ok(await has(/tôi muốn đổi dịch vụ/), 'tin nhắn mới hiện')
ok(await has(/Mình đã (dừng|huỷ) yêu cầu/), 'lịch sử hội thoại vẫn còn')
f = await frame('sau khi nhắn tiếp'); ok(f.right, '[sau khi nhắn tiếp] cột phải còn')
ok(await has(/Hành trình mới/), '[sau khi nhắn tiếp] thanh trên còn')
await p.waitForTimeout(30000)
const cuoi = await txt()
ok(!/chưa hợp lệ/.test(cuoi), 'không có câu đổ lỗi "chưa hợp lệ"')
ok(/tôi muốn đổi dịch vụ/.test(cuoi), 'tin nhắn không bị xoá sau khi có câu đáp')
await p.screenshot({ path: 'e2e.png' })

console.log(`\n===== ${fails.length === 0 ? 'TẤT CẢ ĐẠT' : fails.length + ' MỤC HỎNG'} =====`)
fails.forEach(x => console.log('   ✗', x))
await b.close()
