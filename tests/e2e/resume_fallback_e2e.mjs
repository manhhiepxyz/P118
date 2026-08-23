/**
 * Resume / fallback nhìn TỪ MÀN HÌNH.
 *
 * Backend có thể đúng hoàn toàn mà người dùng vẫn thấy sai — mối nối nằm ở chỗ
 * UI chọn hiển thị field nào. Harness này bám đúng ranh giới đó: sau MỖI lần
 * trạng thái backend đổi, nó đọc lại DB rồi đọc lại màn hình và so hai bên.
 */
import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'

const PW = 'Passw0rd!123'
const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:5274'
const API = process.env.E2E_API ?? 'http://127.0.0.1:8080'
const DB = process.env.E2E_DB ?? 'p118_e2e_db'
const R = []
// Biển số phải DUY NHẤT mỗi lần chạy: provider có ràng buộc (xe, ngày), và
// một biển số cố định làm lần chạy thứ hai nhận 'đã có chỗ đỗ' thay vì đi
// đúng kịch bản.
const plate = () => `51K-${Math.floor(Math.random() * 90000 + 10000)}`
const check = (n, ok, d = '') => { R.push([ok, n]); console.log(`${ok ? 'PASS' : 'FAIL'} | ${n}${d ? `\n       ${d}` : ''}`) }
const sql = q => execFileSync('docker', ['exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', DB, '-tAc', q], { encoding: 'utf8' }).trim().split('\n').filter(Boolean)

const b = await chromium.launch()
const conv = p => p.$$eval('[aria-label="Trao đổi với P-118"] li', e => e.map(x => x.textContent.replace(/\s+/g, ' ').trim()))
const lastSaid = async p => (await conv(p)).slice(-1)[0] ?? ''
const screen = async p => (await p.textContent('body')).replace(/\s+/g, ' ')

async function api(path, { token, method = 'GET', body } = {}) {
  const r = await fetch(`${API}${path}`, {
    method, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, json: await r.json().catch(() => null) }
}

async function signUp(tag, { resident = false, role = null } = {}) {
  const p = await (await b.newContext({ viewport: { width: 1512, height: 1000 } })).newPage()
  const errs = []; p.on('pageerror', e => errs.push(String(e)))
  const U = 'rf' + tag + Math.floor(Math.random() * 1e6)
  await p.goto(`${BASE}/register`)
  await p.fill('#reg-username', U); await p.fill('#reg-email', `${U}@example.test`)
  await p.fill('#reg-password', PW); await p.fill('#reg-confirm', PW)
  await p.click('button[type=submit]'); await p.waitForURL('**/workspace', { timeout: 30000 })
  if (role) sql(`UPDATE users SET role='${role}' WHERE username='${U}'`)
  if (resident) {
    const uid = sql(`SELECT id FROM users WHERE username='${U}'`)[0]
    const rid = sql(`SELECT resident_id FROM residents LIMIT 1`)[0]
    sql(`INSERT INTO user_resident_links (user_id, resident_id, verification_status, verified_at)
         VALUES ('${uid}','${rid}','VERIFIED', now())
         ON CONFLICT (user_id) DO UPDATE SET verification_status='VERIFIED', verified_at=now()`)
    await p.reload(); await p.waitForTimeout(1500)
  }
  const tok = (await api('/api/v1/auth/login', { method: 'POST', body: { username: U, password: PW } })).json?.access_token
  return { p, errs, U, tok }
}

const waitFor = async (fn, ms = 120000, step = 2000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { if (await fn()) return true; await new Promise(r => setTimeout(r, step)) }
  return false
}

async function ask(p, text) {
  await p.fill('textarea', text)
  await p.keyboard.press('Enter')
}

const wfOf = U => sql(`SELECT w.workflow_id FROM workflows w JOIN users u ON u.id=w.owner_user_id
                       WHERE u.username='${U}' AND w.task_plan::text <> 'null' ORDER BY w.created_at DESC LIMIT 1`)[0]

/* ═══ 1. RESUME sau khi đơn vị DUYỆT ═══════════════════════════════════ */
console.log('\n── 1. Đơn vị duyệt → màn hình khách có đi tiếp không ──')
{
  const { p, errs, U } = await signUp('a', { resident: true })
  const prov = await signUp('p', { role: 'provider' })
  await ask(p, `Đăng ký xe máy biển số ${plate()} và giữ chỗ đỗ xe Khu B ngày 2028-09-22`)

  const ok = await waitFor(async () => {
    const w = wfOf(U); if (!w) return false
    return sql(`SELECT count(*) FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)[0] !== '0'
  })
  check('1.1 yêu cầu vào hàng đợi duyệt', ok)
  const w = wfOf(U)

  // CHỜ tới khi UI bắt kịp — nó poll mỗi 1,5s và câu chốt còn qua một lượt
  // gọi model. Kiểm ngay lập tức là đo tốc độ mạng, không đo hợp đồng.
  const sawWait = await waitFor(async () => /chờ đơn vị|đang xác nhận/i.test(await screen(p)), 60000)
  check('1.2 UI nói đang chờ đơn vị', sawWait, (await screen(p)).slice(0, 160))

  for (const t of sql(`SELECT task_id FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)) {
    const r = await api(`/api/v1/service-approvals/${w}/${t}/decide`, { token: prov.tok, method: 'POST', body: { decision: 'approve' } })
    if (r.status !== 200) check(`1.x duyệt ${t}`, false, JSON.stringify(r.json))
  }

  const moved = await waitFor(async () => {
    const st = sql(`SELECT status FROM workflows WHERE workflow_id='${w}'`)[0]
    return st !== 'WAITING_APPROVAL' || sql(`SELECT count(*) FROM payment_approvals WHERE workflow_id='${w}'`)[0] !== '0'
  })
  check('1.3 backend đi tiếp sau khi duyệt', moved,
        `db status=${sql(`SELECT status FROM workflows WHERE workflow_id='${w}'`)[0]}`)

  // Kiểm CÂU CUỐI, không quét cả trang: dải hoạt động giữ lại câu "đang chờ
  // đơn vị" như nhật ký — đúng. Điều phải đổi là câu MỚI NHẤT.
  const uiMoved = await waitFor(
    async () => !/chờ đơn vị cung cấp dịch vụ xác nhận/i.test(await lastSaid(p)) && (await lastSaid(p)) !== '',
    60000,
  )
  check('1.4 câu MỚI NHẤT đi tiếp sau khi đơn vị duyệt', uiMoved, (await lastSaid(p)).slice(0, 160))

  // F5 phải KHÔI PHỤC yêu cầu, không xoá nó.
  //
  // Trước bản vá, `workflow_id` chỉ sống trong React state nên một lần F5 đưa
  // khách về màn hình trống trong khi backend vẫn đang chạy yêu cầu của họ.
  await p.reload(); await p.waitForTimeout(5000)
  const afterReload = await screen(p)
  check('1.5 F5 khôi phục lại yêu cầu đang chạy', /Đặt chỗ đỗ xe|Đăng ký phương tiện/i.test(afterReload)
        && !/làm được gì cho bạn/i.test(afterReload), afterReload.slice(0, 160))
  check('1.6 URL mang workflow_id để F5 tìm lại được', p.url().includes(`w=${w}`), p.url())
  check('1.7 không có lỗi JS', errs.length === 0, errs.join(' | ').slice(0, 200))
  globalThis.__w1 = { p, w, prov, U }
}

/* ═══ 2. FALLBACK khi đơn vị TỪ CHỐI ═══════════════════════════════════ */
console.log('\n── 2. Đơn vị từ chối vì hết chỗ → khách có sửa được không ──')
{
  const { p, errs, U } = await signUp('b', { resident: true })
  const prov = globalThis.__w1.prov
  await ask(p, `Đăng ký xe máy biển số ${plate()} và giữ chỗ đỗ xe Khu B ngày 2028-09-23`)

  const ok = await waitFor(async () => {
    const w = wfOf(U); if (!w) return false
    return sql(`SELECT count(*) FROM service_approvals WHERE workflow_id='${w}' AND tool='book_parking' AND status='AWAITING'`)[0] !== '0'
  })
  check('2.1 chỗ đỗ vào hàng đợi', ok)
  const w = wfOf(U)
  const LY_DO = 'Khu B đã kín chỗ ngày 23/09/2028. Bạn chọn khu khác giúp mình nhé.'

  for (const t of sql(`SELECT task_id FROM service_approvals WHERE workflow_id='${w}' AND status='AWAITING'`)) {
    const tool = sql(`SELECT tool FROM service_approvals WHERE workflow_id='${w}' AND task_id='${t}'`)[0]
    const body = tool === 'book_parking'
      ? { decision: 'reject', reject_code: 'NO_AVAILABILITY', reject_reason: LY_DO }
      : { decision: 'approve' }
    const r = await api(`/api/v1/service-approvals/${w}/${t}/decide`, { token: prov.tok, method: 'POST', body })
    if (r.status !== 200) check(`2.x quyết định ${t}`, false, JSON.stringify(r.json))
  }

  const asked = await waitFor(() => Promise.resolve(
    sql(`SELECT count(*) FROM workflow_clarifications WHERE workflow_id='${w}' AND resolved_at IS NULL`)[0] !== '0'))
  check('2.2 backend mở lượt hỏi lại', asked)

  // Chờ UI bắt kịp: nó poll mỗi 1,5s. Probe riêng đo được ~5s.
  const uiAsked = await waitFor(async () => /kín chỗ/i.test(await screen(p)), 60000)
  const scr = await screen(p)
  const ids = await p.$$eval('input,select,textarea', e => e.map(x => x.id).filter(Boolean))
  check('2.3 UI nói LÝ DO của đơn vị', uiAsked, scr.slice(0, 200))
  check('2.4 UI mở ô để khách trả lời', ids.some(x => /pending-field|parking/.test(x)), ids.join(',').slice(0, 160))
  // Kiểm CÂU CUỐI, không kiểm cả trang: dải hoạt động giữ lại câu "đang chờ
  // đơn vị" như một mục nhật ký, và đó là đúng. Điều sai sẽ là nó vẫn là câu
  // MỚI NHẤT sau khi đơn vị đã quyết xong.
  check('2.5 câu MỚI NHẤT không còn là "đang chờ đơn vị"',
        !/đang chờ đơn vị cung cấp dịch vụ xác nhận/i.test(await lastSaid(p)),
        (await lastSaid(p)).slice(0, 160))
  check('2.6 không có lỗi JS', errs.length === 0, errs.join(' | ').slice(0, 200))
}

console.log('\n══════════════════════════════')
const bad = R.filter(([ok]) => !ok)
console.log(`${R.length - bad.length}/${R.length} PASS`)
bad.forEach(([, n]) => console.log(`  FAIL ${n}`))
await b.close()
process.exit(bad.length ? 1 : 0)
