/**
 * Restart backend GIỮA CHỪNG, rồi reload trình duyệt — thẻ đề xuất vẫn còn.
 *
 * Đây là ca canary trên `p118_e2e_db` đã bắt được một lần: database giữ nguyên
 * đề xuất `PROPOSED` còn hạn, lượt xác nhận vẫn chạy, và giao diện mất hẳn nút
 * "đồng ý". Khách không có đường nào đi tiếp.
 *
 * HAI PHA, và harness lo việc dừng/khởi động backend:
 *
 *   node restart_keeps_the_proposal_card.mjs seed    → gieo, kiểm thấy thẻ
 *   … harness restart backend …
 *   node restart_keeps_the_proposal_card.mjs verify  → reload, kiểm còn thẻ
 *
 * Script KHÔNG tự spawn backend. Một harness browser tự quản vòng đời tiến
 * trình phục vụ là một harness làm hai việc, và khi nó hỏng thì không phân
 * biệt được "sản phẩm hỏng" với "harness không khởi động nổi backend" — đã đo
 * được đúng lần đầu viết nó.
 *
 * Trạng thái giữa hai pha đi qua database, không qua biến trong bộ nhớ: đó
 * cũng chính là thứ đang được kiểm.
 */

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'

const PHA = process.argv[2] ?? 'seed'
const PW = 'Passw0rd!123'
const APP = process.env.P118_APP ?? 'http://127.0.0.1:5290'
const API = process.env.P118_API ?? 'http://127.0.0.1:8100'
const DB = 'p118_e2e_db'
const MOC = '/tmp/p118_restart_probe.json'

const sql = (q) =>
  execFileSync('psql', ['-d', DB, '-tAc', q], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)
const q = (s) => `'${String(s).replace(/'/g, "''")}'`

if (sql('SELECT current_database()')[0] !== DB) {
  console.log('DỪNG: sai database')
  process.exit(2)
}

const loi = []
const check = (ten, dat, ct = '') => {
  console.log(`  ${dat ? '✓' : '✗'} ${ten}${ct ? ` — ${ct}` : ''}`)
  if (!dat) loi.push(ten)
}

const { readFileSync, writeFileSync } = await import('node:fs')

async function dangNhap(page, user) {
  await page.goto(`${APP}/login`)
  await page.fill('#login-username', user)
  await page.fill('#login-password', PW)
  await page.click('button[type=submit]')
  await page.waitForURL('**/workspace', { timeout: 30000 })
}

const b = await chromium.launch()
const page = await (await b.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
const the = () => page.locator('[data-testid="provider-proposal"]')

if (PHA === 'seed') {
  const U = 'rs' + Math.floor(Math.random() * 1e6)
  await fetch(`${API}/api/v1/auth/register`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ username: U, password: PW }),
  })
  const uid = sql(`SELECT id FROM users WHERE username=${q(U)}`)[0]
  const wid = randomUUID()
  const quote = randomUUID()
  const proposal = randomUUID()
  sql(`INSERT INTO workflows (workflow_id, goal, status, owner_user_id)
       VALUES (${q(wid)}::uuid,'Đặt lịch chuyển nhà','WAITING_APPROVAL',${q(uid)}::uuid)`)
  sql(`INSERT INTO workflow_tasks (workflow_id, task_id, tool, status, depends_on, input_data)
       VALUES (${q(wid)}::uuid,'T1','schedule_move','WAITING_APPROVAL','[]'::jsonb,
               '{"move_date":"2026-09-30","move_time":"08:00","move_vehicle":"van","needs_elevator":false,"needs_loading_support":false}'::jsonb)`)
  sql(`INSERT INTO service_quotes (quote_id, external_quote_id, service_provider_id, service_type, amount, currency,
                                   request_fingerprint, valid_until, workflow_id, task_id)
       VALUES (${q(quote)}::uuid, ${q('Q-' + quote.slice(0, 8))}, 'MOV-03','schedule_move',420000,'VND',
               ${q('vt' + wid.slice(0, 8))}, NOW() + INTERVAL '45 min', ${q(wid)}::uuid,'T1')`)
  sql(`INSERT INTO service_provider_proposals (proposal_id, workflow_id, task_id, quote_id, status)
       VALUES (${q(proposal)}::uuid, ${q(wid)}::uuid,'T1',${q(quote)}::uuid,'PROPOSED')`)

  await dangNhap(page, U)
  await page.goto(`${APP}/workspace?w=${wid}`)
  await page.waitForTimeout(2500)
  console.log('Pha SEED — trước restart')
  check('thấy thẻ', (await the().count()) === 1)
  check('đúng proposal vừa gieo', (await the().first().getAttribute('data-proposal-id')) === proposal)
  check('có nút xác nhận', (await page.locator('[data-testid="proposal-confirm"]').count()) === 1)
  writeFileSync(MOC, JSON.stringify({ user: U, wid, proposal }))
} else {
  const { user, wid, proposal } = JSON.parse(readFileSync(MOC, 'utf8'))
  await dangNhap(page, user)
  await page.goto(`${APP}/workspace?w=${wid}`)
  await page.waitForTimeout(3000)
  console.log('Pha VERIFY — sau restart')
  check('thẻ vẫn còn', (await the().count()) === 1)
  check('vẫn đúng proposal cũ', (await the().first().getAttribute('data-proposal-id')) === proposal)
  check('vẫn bấm được', (await page.locator('[data-testid="proposal-confirm"]').count()) === 1)
  check(
    'chưa mở /review',
    Number(sql(`SELECT count(*) FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`)[0]) === 0,
  )
  check(
    'không sinh đề xuất mới',
    Number(sql(`SELECT count(*) FROM service_provider_proposals WHERE workflow_id=${q(wid)}::uuid`)[0]) === 1,
  )

  await page.locator('[data-testid="proposal-confirm"]').click()
  await page.waitForTimeout(3500)
  check(
    'bấm sau restart vẫn ăn, đúng đơn vị',
    sql(`SELECT service_provider_id FROM service_approvals WHERE workflow_id=${q(wid)}::uuid`)[0] === 'MOV-03',
  )
  await page.reload()
  await page.waitForTimeout(2500)
  check('sau đó không mở lại nút', (await page.locator('[data-testid="proposal-confirm"]').count()) === 0)
}

console.log(loi.length ? `\nHỎNG ${loi.length}: ${loi.join(' · ')}` : '\nTẤT CẢ ĐẠT')
await b.close()
process.exit(loi.length ? 1 : 0)
