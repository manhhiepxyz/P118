/**
 * Khung hội thoại phải cuộn NGƯỢC LÊN được tới dòng đầu.
 *
 * Lỗi đã báo: "khung chat không lướt lên được". Nguyên nhân là
 * `justify-content: flex-end` trên khung cuộn — khi nội dung tràn, nó cắt mất
 * phần ĐẦU và trình duyệt không cho cuộn về đó. `margin-top: auto` cho cùng
 * hiệu ứng (ít tin nhắn thì nằm sát đáy) mà vẫn cuộn đủ.
 *
 * Chạy trên BẢN BUILD PRODUCTION: đây là lỗi thuần bố cục CSS, chỉ hiện ra khi
 * khung có chiều cao thật.
 */
import { chromium } from 'playwright'
import fs from 'node:fs'
const APP = process.env.E2E_BASE ?? 'http://127.0.0.1:5299'
const S = '/tmp/claude-501/-Users-thanhtin-P-118/280ead94-a097-4bae-ba74-fea75c93cdbb/scratchpad/'
const token = fs.readFileSync(S + 'tok4.txt', 'utf8').trim()
const wid = fs.readFileSync(S + 'wf4.txt', 'utf8').trim()
let ma = 0
const kiem = (d, t) => { console.log(`${d ? '  PASS' : '  FAIL'}  ${t}`); if (!d) ma = 1 }

const b = await chromium.launch()
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } })
await pg.goto(APP + '/', { waitUntil: 'domcontentloaded' })
await pg.evaluate(([k, t]) => sessionStorage.setItem(k, t), ['p118.access_token', token])
await pg.goto(APP + `/workspace?w=${wid}`, { waitUntil: 'domcontentloaded' })
await pg.waitForSelector('textarea', { timeout: 15000 })
await pg.waitForTimeout(6000)

// Ép khung nhỏ lại để nội dung chắc chắn tràn.
const sep = pg.locator('[role="separator"]')
if (await sep.count()) { await sep.focus(); await pg.keyboard.press('End'); await pg.waitForTimeout(500) }

const o = await pg.evaluate(() => {
  const ol = document.querySelector('[data-chat-transcript]')
  if (!ol) return null
  const box = ol.closest('.overflow-y-auto')
  return { tran: box.scrollHeight > box.clientHeight + 4, cuonToiDa: box.scrollHeight - box.clientHeight }
})
kiem(o !== null, 'tìm thấy khung hội thoại')
kiem(o?.tran === true, `nội dung có tràn để thử cuộn (dư ${o?.cuonToiDa}px)`)

const veDau = await pg.evaluate(() => {
  const box = document.querySelector('[data-chat-transcript]').closest('.overflow-y-auto')
  box.scrollTop = 0
  return box.scrollTop
})
kiem(veDau === 0, `cuộn được về dòng đầu (scrollTop=${veDau})`)

// Dòng đầu phải NHÌN THẤY được, không bị cắt trên mép khung.
const hienDu = await pg.evaluate(() => {
  const box = document.querySelector('[data-chat-transcript]').closest('.overflow-y-auto')
  const dau = document.querySelector('[data-chat-transcript] > li')
  if (!dau) return null
  return Math.round(dau.getBoundingClientRect().top - box.getBoundingClientRect().top)
})
kiem(hienDu !== null && hienDu >= -1, `dòng đầu không bị cắt trên mép (lệch ${hienDu}px)`)

await b.close()
process.exit(ma)
