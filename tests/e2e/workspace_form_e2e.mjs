import { chromium } from 'playwright'
const PW='Passw0rd!123'
const OUT='/private/tmp/claude-501/-Users-thanhtin-P-118/280ead94-a097-4bae-ba74-fea75c93cdbb/scratchpad'
const D = String(11+Math.floor(Math.random()*16)).padStart(2,'0')
const b=await chromium.launch()
const conv = p => p.$$eval('[aria-label="Trao đổi với P-118"] li', e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim()))

async function run(label, fill) {
  const p = await (await b.newContext({viewport:{width:1512,height:1000}})).newPage()
  const errs=[]; p.on('pageerror',e=>errs.push(String(e)))
  const U='fm'+Math.floor(Math.random()*1e6)
  await p.goto('http://localhost:5273/register')
  await p.fill('#reg-username',U); await p.fill('#reg-email',`${U}@example.test`)
  await p.fill('#reg-password',PW); await p.fill('#reg-confirm',PW)
  await p.click('button[type=submit]'); await p.waitForURL('**/workspace',{timeout:25000})
  await p.waitForTimeout(1200)
  await fill(p)
  await p.click('.console-run')
  // Chờ tới khi CANVAS có chặng, không chỉ chờ hội thoại có 2 dòng: câu
  // "Đang chuẩn bị kế hoạch" đến ngay lập tức và dừng ở đó là đo hụt.
  for (let i=0;i<60;i++){
    await p.waitForTimeout(2000)
    if (await p.$$eval('.react-flow__node', e=>e.length) > 0) break
    if ((await conv(p)).some(t=>/gõ lặp|chưa hiểu/.test(t))) break
  }
  const c = await conv(p)
  const spam = c.some(t=>/gõ lặp/.test(t))
  const nodes = await p.$$eval('.react-flow__node', e=>e.length)
  console.log(`${spam ? 'FAIL' : 'PASS'} | ${label}`)
  console.log(`       gửi : ${c[0]?.slice(0,120)}`)
  console.log(`       đáp : ${c.at(-1)?.slice(0,120)}`)
  console.log(`       chặng trên canvas: ${nodes}${errs.length?' | LỖI '+errs[0]:''}`)
  await p.screenshot({path:`${OUT}/form-${label.replace(/\W+/g,'-')}.png`})
  await p.context().close()
  return !spam && nodes > 0
}

const ok = []
ok.push(await run('tham quan, tự đi', async p => {
  await p.locator('ul.seq > li button[aria-pressed]').first().click(); await p.waitForTimeout(400)
  await p.selectOption('#f-project','Vinhomes Ocean Park')
  await p.fill('#shared-date',`2027-04-${D}`)
  await p.selectOption('#f-time','10:00')
  await p.selectOption('#f-needs_shuttle','false')
}))
ok.push(await run('tham quan + xe đón', async p => {
  await p.locator('ul.seq > li button[aria-pressed]').first().click(); await p.waitForTimeout(400)
  await p.selectOption('#f-project','Vinhomes Sài Gòn Park')
  await p.fill('#shared-date',`2027-05-${D}`)
  await p.selectOption('#f-time','09:00')
  await p.selectOption('#f-needs_shuttle','true'); await p.waitForTimeout(300)
  await p.fill('#f-pickup_note','Landmark 81')
  await p.selectOption('#f-pickup_time_note','07:00')
  await p.fill('#f-pickup_phone','0901234567')
}))
ok.push(await run('đăng ký tư vấn', async p => {
  await p.locator('ul.seq > li button[aria-pressed]').nth(1).click(); await p.waitForTimeout(400)
  await p.selectOption('#f-project','Vinhomes Ocean Park')
  await p.selectOption('#f-interest_type','buy')
  await p.selectOption('#f-preferred_contact_time','09:00')
  await p.selectOption('#f-consent','true')
}))
console.log('\n══ TỔNG: '+ok.filter(Boolean).length+'/'+ok.length+' ══')
await b.close()
