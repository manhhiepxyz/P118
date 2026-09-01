/**
 * Browser E2E: khách nhìn thấy đề xuất đơn vị và bấm đồng ý.
 *
 * Chạy:
 *   # backend trên p118_e2e_db, cờ bật
 *   DATABASE_URL=postgresql://…/p118_e2e_db SERVICE_PROVIDER_MATCHING=1 \
 *     uvicorn src.main:app --host 127.0.0.1 --port 8100
 *   cd frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:8100 npm run dev -- --port 5273
 *   node tests/e2e/provider_proposal_journey.mjs
 *
 * KHÔNG gọi model. Dữ liệu gieo bằng SQL tất định — đường qua model đã có
 * canary riêng, và một suite browser phụ thuộc vào model là một suite nhấp
 * nháy. Thứ đang được kiểm ở đây là GIAO DIỆN và hai endpoint thật nó gọi
 * (`GET /workflows/demo/{id}`, `POST /service-proposals/{id}/confirm`), không
 * phải lượt lập kế hoạch.
 *
 * Database: CHỈ `p118_e2e_db`. Không chạm `p118_db`, không in DSN/token.
 */

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'

const PW = 'Passw0rd!123'
const APP = process.env.P118_APP ?? 'http://127.0.0.1:5273'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'

const sql = (q) =>
  execFileSync('psql', ['-d', DB, '-tAc', q], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)

/* GUARD: đọc tên database THẬT từ chính kết nối, không tin biến. Một `.env`
   trỏ sai là cách người ta gieo dữ liệu test vào production trong lúc định thử
   ở staging. */
const duoc = sql('SELECT current_database()')[0]
if (duoc !== DB) {
  console.log(`DỪNG: đang kết nối ${duoc}, không phải ${DB}`)
  process.exit(2)
}
console.log(`database: ${duoc}`)

const api = async (p, { token, method = 'GET', body } = {}) => {
  const r = await fetch(`${API}/api/v1${p}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}

const q = (s) => `'${String(s).replace(/'/g, "''")}'`
const loi = []
const check = (ten, dat, chi_tiet = '') => {
  console.log(`  ${dat ? '✓' : '✗'} ${ten}${chi_tiet ? ` — ${chi_tiet}` : ''}`)
  if (!dat) loi.push(ten)
}

/** Gieo một workflow đang chờ khách chọn đơn vị, với `n` bước độc lập. */
function gieo(uid, n) {
  const wid = randomUUID()
  sql(
    `INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
     VALUES (${q(wid)}::uuid, 'Đặt lịch chuyển nhà', 'WAITING_APPROVAL', ${q(uid)}::uuid)`,
  )
  const don_vi = ['MOV-03', 'MOV-02']
  const gia = [420000, 470000]
  const ra = []
  for (let i = 0; i < n; i++) {
    const task = `T${i + 1}`
    const input = JSON.stringify({
      move_date: `2026-09-${20 + i}`,
      move_time: '08:00',
      move_vehicle: 'van',
      needs_elevator: false,
      needs_loading_support: false,
    })
    sql(
      `INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)
       VALUES (${q(wid)}::uuid, ${q(task)}, 'schedule_move', 'WAITING_APPROVAL', '[]'::jsonb, ${q(input)}::jsonb)`,
    )
    const quote = randomUUID()
    sql(
      `INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount,
                                   currency, request_fingerprint, valid_until, workflow_id, task_id)
       VALUES (${q(quote)}::uuid, ${q('Q-' + quote.slice(0, 8))}, ${q(don_vi[i])}, 'schedule_move', ${gia[i]},
               'VND', ${q('vt' + i + wid.slice(0, 8))}, NOW() + INTERVAL '30 min', ${q(wid)}::uuid, ${q(task)})`,
    )
    const proposal = randomUUID()
    sql(
      `INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status)
       VALUES (${q(proposal)}::uuid, ${q(wid)}::uuid, ${q(task)}, ${q(quote)}::uuid, 'PROPOSED')`,
    )
    ra.push({ task, proposal, quote, don_vi: don_vi[i], gia: gia[i] })
  }
  return { wid, buoc: ra }
}

const dem = (wid, bang, dieu_kien = '') =>
  Number(sql(`SELECT count(*) FROM ${bang} WHERE workflow_id=${q(wid)}::uuid ${dieu_kien}`)[0])

const b = await chromium.launch()
const page = await (await b.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
const jsErr = []
page.on('pageerror', (e) => jsErr.push(String(e)))

const U = 'pp' + Math.floor(Math.random() * 1e6)
await api('/auth/register', { method: 'POST', body: { username: U, password: PW } })
const tok = (await api('/auth/login', { method: 'POST', body: { username: U, password: PW } })).json.access_token
const uid = sql(`SELECT id FROM users WHERE username=${q(U)}`)[0]

/* Đăng nhập TRÊN TRÌNH DUYỆT: token phải nằm ở nơi ứng dụng thật cất nó, không
   phải nơi harness đoán. */
await page.goto(`${APP}/login`)
await page.fill('#login-username', U)
await page.fill('#login-password', PW)
await page.click('button[type=submit]')
await page.waitForURL('**/workspace', { timeout: 30000 })

const the = () => page.locator('[data-testid="provider-proposal"]')
const mo = async (wid) => {
  await page.goto(`${APP}/workspace?w=${wid}`)
  await page.waitForTimeout(2500)
}

// ===================================================== Case 1 — một đề xuất
console.log('\nCase 1 — một đề xuất: thấy thẻ, F5 vẫn thấy, bấm xong đúng đơn vị nhận việc')
{
  const { wid, buoc } = gieo(uid, 1)
  await mo(wid)
  check('thấy đúng 1 thẻ', (await the().count()) === 1, `đếm được ${await the().count()}`)
  check('hiện TÊN đơn vị, không hiện mã', (await page.textContent('body')).includes('Dịch vụ An Khang'))
  check('không hiện mã kỹ thuật', !(await page.textContent('body')).includes('MOV-03'))
  check('không hiện quote_id', !(await page.textContent('body')).includes(buoc[0].quote.slice(0, 12)))
  check('hiện giá', (await page.textContent('body')).includes('420.000'))
  check('có nút xác nhận', (await page.locator('[data-testid="proposal-confirm"]').count()) === 1)

  await page.reload()
  await page.waitForTimeout(2500)
  check('F5 trước khi bấm: thẻ vẫn còn', (await the().count()) === 1)

  check('trước khi bấm: /review chưa có việc', dem(wid, 'service_approvals') === 0)
  await page.locator('[data-testid="proposal-confirm"]').first().click()
  await page.waitForTimeout(3500)
  check('sau khi bấm: đúng 1 dòng duyệt', dem(wid, 'service_approvals') === 1)
  check(
    'chủ sở hữu lấy từ chứng từ',
    sql(`SELECT service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`)[0] === 'MOV-03',
  )
  check('đề xuất đã CONFIRMED', dem(wid, 'service_provider_proposals', "AND status='CONFIRMED'") === 1)
  check('sau khi bấm: không còn nút', (await page.locator('[data-testid="proposal-confirm"]').count()) === 0)

  await page.reload()
  await page.waitForTimeout(2500)
  check('F5 sau khi bấm: không mở lại nút', (await page.locator('[data-testid="proposal-confirm"]').count()) === 0)
  check('F5 sau khi bấm: không sinh đề xuất mới', dem(wid, 'service_provider_proposals') === 1)
}

// ===================================================== Case 2 — hai đề xuất
console.log('\nCase 2 — hai đề xuất độc lập: bấm A không đụng B, không cross-wire')
{
  const { wid, buoc } = gieo(uid, 2)
  await mo(wid)
  check('thấy đúng 2 thẻ', (await the().count()) === 2, `đếm được ${await the().count()}`)
  const ids = await the().evaluateAll((els) => els.map((e) => e.dataset.proposalId))
  check('mỗi thẻ mang proposal_id riêng', new Set(ids).size === 2)
  const tasks = await the().evaluateAll((els) => els.map((e) => e.dataset.taskId))
  check('thẻ gắn đúng bước', JSON.stringify(tasks) === JSON.stringify(['T1', 'T2']), tasks.join(','))

  const theA = the().filter({ has: page.locator(`[data-testid="proposal-confirm"]`) }).first()
  await theA.locator('[data-testid="proposal-confirm"]').click()
  await page.waitForTimeout(3500)

  check('bấm A: đúng 1 dòng duyệt', dem(wid, 'service_approvals') === 1)
  check(
    'dòng duyệt thuộc bước A',
    sql(`SELECT task_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`)[0] === buoc[0].task,
  )
  check(
    'đề xuất B vẫn PROPOSED',
    sql(`SELECT status FROM service_provider_proposals WHERE proposal_id=${q(buoc[1].proposal)}::uuid`)[0] ===
      'PROPOSED',
  )
  check('màn hình còn đúng 1 thẻ bấm được', (await page.locator('[data-testid="proposal-confirm"]').count()) === 1)
  const conLai = await the().evaluateAll((els) => els.map((e) => e.dataset.proposalId))
  check('thẻ còn lại là B', conLai.length === 1 && conLai[0] === buoc[1].proposal)

  await page.locator('[data-testid="proposal-confirm"]').click()
  await page.waitForTimeout(3500)
  const chu = sql(
    `SELECT task_id || '=' || service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid ORDER BY task_id`,
  )
  check('hai dòng duyệt, đúng hai đơn vị', JSON.stringify(chu) === JSON.stringify(['T1=MOV-03', 'T2=MOV-02']), chu.join(' '))
  check('không còn thẻ nào', (await the().count()) === 0)
}

// ===================================================== Case 3 — hết hạn
console.log('\nCase 3 — báo giá hết hạn: không nút, nói đúng lý do, không nhắc thanh toán')
{
  const { wid } = gieo(uid, 1)
  sql(`UPDATE service_quotes SET valid_until = NOW() - INTERVAL '1 min' WHERE workflow_id=${q(wid)}::uuid`)
  await mo(wid)
  const body = (await page.textContent('body')).toLowerCase()
  check('không dựng nút xác nhận', (await page.locator('[data-testid="proposal-confirm"]').count()) === 0)
  check('không nhắc thanh toán', !body.includes('thanh toán'))
  check('không tự mở /review', dem(wid, 'service_approvals') === 0)
}

// ===================================================== Case 4 — không đề xuất
console.log('\nCase 4 — workflow không có đề xuất: không thẻ nào (hành vi legacy)')
{
  const wid = randomUUID()
  sql(
    `INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
     VALUES (${q(wid)}::uuid, 'Đăng ký xe', 'WAITING_APPROVAL', ${q(uid)}::uuid)`,
  )
  sql(
    `INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on)
     VALUES (${q(wid)}::uuid, 'T1', 'register_vehicle', 'WAITING_APPROVAL', '[]'::jsonb)`,
  )
  sql(
    `INSERT INTO service_approvals (workflow_id, task_id, tool, service_label, details, status, service_provider_id)
     VALUES (${q(wid)}::uuid, 'T1', 'register_vehicle', 'Đăng ký phương tiện', '{}'::jsonb, 'AWAITING', 'BQL-PARK')`,
  )
  await mo(wid)
  check('không thẻ đề xuất nào', (await the().count()) === 0)
  check('không sinh chứng từ', dem(wid, 'service_quotes') === 0)
}

console.log('\nlỗi JS trên trang:', jsErr.length ? jsErr.join(' | ') : 'không')
console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
