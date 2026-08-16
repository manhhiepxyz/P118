/**
 * E2E workspace ↔ backend thật (stack Docker, cổng 8080).
 * Chạy: node e2e.mjs
 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const PW = 'Passw0rd!123'
const BASE = 'http://localhost:5273'
const OUT = '/private/tmp/claude-501/-Users-thanhtin-P-118/280ead94-a097-4bae-ba74-fea75c93cdbb/scratchpad'
const R = []
const plate = () => `30A-${Math.floor(Math.random()*900+100)}.${Math.floor(Math.random()*90+10)}`
const check = (n, ok, d='') => { R.push([ok,n]); console.log(`${ok?'PASS':'FAIL'} | ${n}${d?`\n       ${d}`:''}`) }
const sql = q => execFileSync('docker',['exec','p118_postgres','psql','-U','p118','-d','p118_db','-tAc',q],{encoding:'utf8'}).trim().split('\n').filter(Boolean)

const b = await chromium.launch()
const conv = p => p.$$eval('[aria-label="Trao đổi với P-118"] li', e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim()))
const nodes = p => p.$$eval('.react-flow__node', e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim().slice(0,50)))
const card = p => p.$eval('#pending-title', e=>e.textContent).catch(()=>null)

async function signUp(tag, { resident = false } = {}) {
  const p = await (await b.newContext({viewport:{width:1512,height:1000}})).newPage()
  const errs = []; p.on('pageerror', e => errs.push(String(e)))
  const U = 'e2e'+tag+Math.floor(Math.random()*1e6)
  await p.goto(`${BASE}/register`)
  await p.fill('#reg-username',U); await p.fill('#reg-email',`${U}@example.test`)
  await p.fill('#reg-password',PW); await p.fill('#reg-confirm',PW)
  await p.click('button[type=submit]'); await p.waitForURL('**/workspace',{timeout:25000})
  if (resident) {
    const uid = sql(`SELECT id FROM users WHERE username='${U}'`)[0]
    const rid = sql(`SELECT resident_id FROM residents LIMIT 1`)[0]
    sql(`INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)
         VALUES ('${uid}','${rid}','VERIFIED', now())
         ON CONFLICT (user_id) DO UPDATE SET verification_status='VERIFIED', verified_at=now()`)
    await p.reload(); await p.waitForTimeout(1500)
  }
  return { p, errs, U }
}

/** Chờ tới khi hội thoại có ít nhất `n` lượt, hoặc hết giờ. */
async function waitTurns(p, n, ms=90000) {
  const t0 = Date.now()
  while (Date.now()-t0 < ms) {
    if ((await conv(p)).length >= n) return true
    await p.waitForTimeout(1000)
  }
  return false
}
const waitCard = async (p, ms=120000) => {
  const t0 = Date.now()
  while (Date.now()-t0 < ms) { if (await card(p)) return true; await p.waitForTimeout(1500) }
  return false
}

/* ══ 1. Đặt lịch tham quan qua FORM ═══════════════════════════════════ */
console.log('\n── 1. Đặt lịch tham quan (form → backend thật) ──')
{
  const { p, errs } = await signUp('a')
  await p.locator('ul.seq > li button[aria-pressed]').first().click(); await p.waitForTimeout(400)
  await p.selectOption('#f-project','Vinhomes Ocean Park')
  await p.fill('#shared-date','2026-09-20')
  await p.selectOption('#f-time','10:00')
  await p.selectOption('#f-needs_shuttle','false')
  await p.click('.console-run')
  check('gửi yêu cầu, chuyển sang canvas', await waitTurns(p,1,20000))
  const got = await p.waitForFunction(()=>document.querySelectorAll('.react-flow__node').length>0,{timeout:90000}).then(()=>true).catch(()=>false)
  const ns = await nodes(p)
  check('canvas dựng từ plan THẬT của backend', got && ns.length>0, `${ns.length} chặng: ${ns.join(' | ')}`)
  await waitTurns(p, 2, 90000)
  const c = await conv(p)
  check('P-118 trả lời trong hội thoại', c.length>=2, c.at(-1)?.slice(0,140))
  check('không lỗi runtime', errs.filter(e=>!/notifications/.test(e)).length===0, errs.join(' | ').slice(0,120))
  await p.screenshot({path:`${OUT}/e2e-1.png`})
  await p.context().close()
}

/* ══ 2. Mô tả bằng LỜI, không chọn dịch vụ ════════════════════════════ */
console.log('\n── 2. Mô tả bằng lời qua ô hội thoại ──')
{
  const { p, errs } = await signUp('b')
  await p.fill('#ws-composer','Tôi muốn xem nhà ở Vinhomes Ocean Park')
  await p.click('.console-run')
  check('bắt đầu được từ câu tự nhiên', await waitTurns(p,2,90000))
  const c = await conv(p)
  const cardTitle = await card(p)
  check('backend hỏi lại khi thiếu thông tin HOẶC chạy tiếp', c.length>=2, `card=${cardTitle} | ${c.at(-1)?.slice(0,120)}`)
  check('không lỗi runtime', errs.filter(e=>!/notifications/.test(e)).length===0, errs.join(' | ').slice(0,120))
  await p.screenshot({path:`${OUT}/e2e-2.png`})
  await p.context().close()
}

/* ══ 3. Cư dân: đỗ xe → chờ duyệt thanh toán → HỎI THÊM → duyệt bằng NÚT ══ */
console.log('\n── 3. Thanh toán: hỏi thêm rồi duyệt bằng NÚT ──')
{
  const { p, errs, U } = await signUp('c', { resident: true })
  await p.fill('#ws-composer',`Đăng ký xe ${plate()} là ô tô và đặt chỗ đỗ xe Khu B ngày 2026-09-22`)
  await p.click('.console-run')
  const appeared = await waitCard(p, 180000)
  check('backend đưa ra việc chờ duyệt', appeared, `thẻ = ${await card(p)}`)
  const aside = await p.textContent('aside').catch(()=>'')
  check('cột phải hiện ngữ cảnh có cấu trúc (số tiền)', /Số tiền|Mã đặt chỗ/.test(aside), aside.replace(/\s+/g,' ').slice(0,150))

  // hỏi thêm — trạng thái KHÔNG được đổi
  const before = await card(p)
  const nBefore = (await conv(p)).length
  await p.fill('#ws-composer','Khoản này là phí gì?'); await p.click('.console-run')
  await waitTurns(p, nBefore+2, 20000)
  const after = await card(p)
  check('hỏi thêm KHÔNG làm đổi trạng thái chờ duyệt', !!after && after === before, `trước=${before} sau=${after}`)
  check('P-118 giải thích từ ngữ cảnh', (await conv(p)).at(-1)?.length > 10, (await conv(p)).at(-1)?.slice(0,130))
  await p.screenshot({path:`${OUT}/e2e-3-waiting.png`})

  // duyệt bằng NÚT
  const nBtn = (await conv(p)).length
  await p.click('button:has-text("Xác nhận thanh toán")')
  await waitTurns(p, nBtn+1, 60000)
  const wf = sql(`SELECT status FROM workflows WHERE owner_user_id=(SELECT id FROM users WHERE username='${U}') ORDER BY created_at DESC LIMIT 1`)[0]
  const paid = sql(`SELECT count(*) FROM payments WHERE payment_status IN ('PAID','PENDING')`)[0]
  check('bấm nút → backend ghi nhận quyết định', wf !== 'WAITING_APPROVAL', `workflow.status=${wf}, payments=${paid}`)
  check('không lỗi runtime', errs.length===0, errs.join(' | ').slice(0,140))
  await p.screenshot({path:`${OUT}/e2e-3-approved.png`})
  await p.context().close()
}

/* ══ 4. Từ chối bằng CÂU NÓI ═════════════════════════════════════════ */
console.log('\n── 4. Từ chối bằng câu nói tự nhiên ──')
{
  const { p, errs, U } = await signUp('d', { resident: true })
  await p.fill('#ws-composer',`Đăng ký xe ${plate()} là ô tô và đặt chỗ đỗ xe Khu B ngày 2026-09-23`)
  await p.click('.console-run')
  const appeared = await waitCard(p, 180000)
  check('có việc chờ duyệt', appeared, `thẻ = ${await card(p)}`)
  const n = (await conv(p)).length
  await p.fill('#ws-composer','Tôi chưa muốn thanh toán'); await p.click('.console-run')
  await waitTurns(p, n+2, 60000)
  const wf = sql(`SELECT status FROM workflows WHERE owner_user_id=(SELECT id FROM users WHERE username='${U}') ORDER BY created_at DESC LIMIT 1`)[0]
  check('câu phủ định → TỪ CHỐI, không phải duyệt', wf !== 'WAITING_APPROVAL' && wf !== 'SUCCESS', `workflow.status=${wf}`)
  check('P-118 xác nhận đã dừng', /dừng|từ chối|không/i.test((await conv(p)).at(-1)||''), (await conv(p)).at(-1)?.slice(0,130))
  check('không lỗi runtime', errs.length===0, errs.join(' | ').slice(0,140))
  await p.screenshot({path:`${OUT}/e2e-4.png`})
  await p.context().close()
}

console.log('\n══ TỔNG: '+R.filter(r=>r[0]).length+'/'+R.length+' ══')
await b.close()
process.exit(R.every(r=>r[0]) ? 0 : 1)
