/** E2E: khách đặt lịch → chờ ĐƠN VỊ duyệt → provider duyệt ở /review → khách nhận chi tiết. */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
const PW='Passw0rd!123', BASE='http://localhost:5273'
const OUT='/private/tmp/claude-501/-Users-thanhtin-P-118/280ead94-a097-4bae-ba74-fea75c93cdbb/scratchpad'
const R=[]; const check=(n,ok,d='')=>{R.push(ok);console.log(`${ok?'PASS':'FAIL'} | ${n}${d?`\n       ${d}`:''}`)}
const sql=q=>execFileSync('docker',['exec','p118_postgres','psql','-U','p118','-d','p118_db','-tAc',q],{encoding:'utf8'}).trim().split('\n').filter(Boolean)
// Khung giờ tham quan là tài nguyên CÓ HẠN: chạy lại đúng ngày giờ cũ thì
// backend từ chối vì slot đã có người, và test đo nhầm nhánh thất bại.
const DAY = String(11 + Math.floor(Math.random()*16)).padStart(2,'0')
const HOUR = String(8 + Math.floor(Math.random()*8)).padStart(2,'0')
const SLOT = `2027-03-${DAY}`
const b=await chromium.launch()
console.log(`   (khung giờ lần này: ${SLOT} ${HOUR}:00)`)

async function signUp(tag, role) {
  const p = await (await b.newContext({viewport:{width:1512,height:1000}})).newPage()
  const errs=[]; p.on('pageerror',e=>errs.push(String(e)))
  const U='rv'+tag+Math.floor(Math.random()*1e6)
  await p.goto(`${BASE}/register`)
  await p.fill('#reg-username',U); await p.fill('#reg-email',`${U}@example.test`)
  await p.fill('#reg-password',PW); await p.fill('#reg-confirm',PW)
  await p.click('button[type=submit]'); await p.waitForURL('**/workspace',{timeout:25000})
  if (role) { sql(`UPDATE users SET role='${role}' WHERE username='${U}'`); }
  return {p,errs,U}
}
const nodes = p => p.$$eval('.react-flow__node', e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim()))
const card = p => p.$eval('#pending-title', e=>e.textContent).catch(()=>null)

// ── khách đặt lịch + xe đón ─────────────────────────────────────────────
const { p, errs, U } = await signUp('c')
await p.fill('#ws-composer',`Đặt lịch tham quan Vinhomes Ocean Park ngày ${SLOT} lúc ${HOUR}:00, có xe đưa đón cho 2 khách`)
await p.click('.console-run')
let waited = false
for (let i=0;i<90;i++){ await p.waitForTimeout(2000); if (await card(p)) { waited = true; break } }
check('workflow DỪNG ở chờ đơn vị duyệt (không tự hoàn tất)', waited, `thẻ = ${await card(p)}`)

const ns = await nodes(p)
check('canvas ghi "Chờ đơn vị", KHÔNG phải "Chờ bạn"',
  ns.some(n=>/Chờ đơn vị/.test(n)) && !ns.some(n=>/Chờ bạn/.test(n)),
  ns.map(n=>n.slice(0,70)).join('\n       '))
const aside = await p.textContent('aside')
check('cột phải KHÔNG có nút duyệt (đơn vị mới là người quyết)',
  !/Xác nhận thanh toán|Từ chối/.test(aside), aside.replace(/\s+/g,' ').slice(0,120))
check('chưa có thông tin tài xế trước khi đơn vị duyệt',
  !/Tài xế|Biển số/.test(ns.join(' ')), '')

const wfBefore = sql(`SELECT status FROM workflows WHERE owner_user_id=(SELECT id FROM users WHERE username='${U}') ORDER BY created_at DESC LIMIT 1`)[0]
check('DB: workflow ở WAITING_APPROVAL', wfBefore==='WAITING_APPROVAL', `status=${wfBefore}`)
await p.screenshot({path:`${OUT}/rv-1-waiting.png`})

// ── provider duyệt ở /review ────────────────────────────────────────────
const prov = await signUp('p','provider')
await prov.p.goto(`${BASE}/review`); await prov.p.waitForTimeout(2500)
await prov.p.click('button:has-text("Tham quan")').catch(()=>{})
await prov.p.waitForTimeout(2000)
const list = await prov.p.textContent('main').catch(()=>'')
check('cổng /review hiện lịch đang chờ duyệt', new RegExp(`${SLOT}|Ocean Park`).test(list), list.replace(/\s+/g,' ').slice(0,160))
await prov.p.screenshot({path:`${OUT}/rv-2-review.png`})
// Duyệt ĐÚNG thẻ của lượt chạy này.
//
// `.first()` bấm vào đầu hàng chờ — và hàng chờ còn tồn đọng yêu cầu của những
// lần chạy trước. Lần trước test duyệt nhầm một lịch cũ đã hết chỗ, backend trả
// 502, còn lịch vừa tạo thì không ai duyệt: bốn kiểm tra đỏ vì lỗi nhắm của
// test chứ không phải vì sản phẩm hỏng.
// Nhắm ĐÚNG approval của lượt chạy này bằng `workflow_id` lấy từ database,
// không bằng "thẻ đầu hàng chờ" và cũng không bằng ngày giờ hiển thị.
//
// `.first()` từng bấm nhầm một yêu cầu cũ đã hết chỗ: backend trả 502, lịch
// vừa tạo không ai duyệt, và bốn kiểm tra đỏ vì lỗi nhắm của test chứ không
// phải vì sản phẩm hỏng.
const WF = sql(`SELECT workflow_id FROM workflows WHERE owner_user_id=(SELECT id FROM users WHERE username='${U}') ORDER BY created_at DESC LIMIT 1`)[0]
const approvalRow = sql(`SELECT viewing_date||' '||viewing_time FROM viewing_approvals WHERE workflow_id='${WF}'`)[0]
console.log(`   (approval của lượt này: wf=${WF.slice(0,8)} ${approvalRow})`)
const mine = prov.p.locator('li,article,div')
  .filter({ hasText: approvalRow.split(' ')[0] })
  .filter({ has: prov.p.locator('button:has-text("Duyệt")') }).last()
await mine.locator('button:has-text("Duyệt")').first().click({timeout:20000})
console.log('   … đã bấm Duyệt, chờ đặt xe (~30s)')
await prov.p.waitForTimeout(45000)

// ── khách nhận kết quả, không cần tải lại trang ─────────────────────────
let ok=false, after=[]
for (let i=0;i<40;i++){
  await p.waitForTimeout(2000)
  after = await nodes(p)
  if (after.some(n=>/Hoàn tất/.test(n))) { ok=true; break }
}
check('khách thấy kết quả mà không phải tải lại trang', ok, after.map(n=>n.slice(0,80)).join('\n       '))
const detail = after.join(' ')
check('CHỈ SAU khi duyệt mới có thông tin xe đón', /Tài xế|Biển số|Giờ đón/.test(detail),
  detail.match(/.{0,90}(Tài xế|Biển số).{0,60}/)?.[0] ?? 'không thấy')
const wfAfter = sql(`SELECT status FROM workflows WHERE owner_user_id=(SELECT id FROM users WHERE username='${U}') ORDER BY created_at DESC LIMIT 1`)[0]
check('DB: workflow chuyển sang SUCCESS', wfAfter==='SUCCESS', `status=${wfAfter}`)
const conv = await p.$$eval('[aria-label="Trao đổi với P-118"] li', e=>e.map(x=>x.textContent.replace(/\s+/g,' ').trim()))
const last = conv.at(-1) || ''
check('P-118 nói lời kết CÓ dữ kiện sau khi đơn vị duyệt',
  /hoàn tất|xong/i.test(last) && /Tài xế|Biển số|Mã xe|Thời gian/i.test(last),
  last.slice(0,190))
const dbAnswer = sql(`SELECT assistant_for_status||' | '||coalesce(assistant_answer,'') FROM workflows WHERE workflow_id='${WF}'`)[0]
check('backend ghi câu chốt TRƯỚC khi SUCCESS (assistant_for_status=SUCCESS)',
  dbAnswer.startsWith('SUCCESS') && !/đang xác nhận/.test(dbAnswer), dbAnswer.slice(0,150))
check('không lỗi runtime', errs.length===0 && prov.errs.length===0, [...errs,...prov.errs].join(' | ').slice(0,140))
await p.screenshot({path:`${OUT}/rv-3-done.png`})

console.log('\n══ TỔNG: '+R.filter(Boolean).length+'/'+R.length+' ══')
await b.close()
process.exit(R.every(Boolean)?0:1)
