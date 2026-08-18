/**
 * Browser E2E thật — Playwright + Chromium, chạy trên STACK DOCKER.
 *
 * Khác các lượt trước: React trỏ vào backend Docker (cổng 8080), không phải một
 * uvicorn local. Đó chính là tầng đã bỏ lọt sự cố "Compose healthy nhưng mọi
 * workflow chết": browser E2E cũ chạy backend local với cấu hình đúng nên không
 * bao giờ nhìn thấy cấu hình sai của Compose.
 *
 * Chạy:
 *   sh scripts/stack_up.sh
 *   cd frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:$APP_PORT npm run dev -- --port 5273
 *   node tests/e2e/browser_acceptance.mjs
 *
 * Database: p118_db của stack hiện tại. Không đọc/in API key, token hay DSN.
 */

import { chromium } from 'playwright'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const APP = process.env.P118_APP ?? 'http://127.0.0.1:5273'

/**
 * Cổng host của backend, phân giải ĐÚNG như docker compose phân giải nó.
 *
 * Compose đọc `${APP_PORT:-8080}`: biến môi trường trước, rồi `.env` ở gốc
 * repo. Harness này trước đây hardcode 8080, nên với `APP_PORT=8000` trong
 * `.env` — cấu hình đang dùng thật — mọi lệnh gọi API đều rơi vào một cổng
 * không ai lắng nghe. Kết quả không phải "harness hỏng" mà là một tràng check
 * đỏ trông y hệt lỗi sản phẩm.
 */
function appPort() {
  if (process.env.APP_PORT) return process.env.APP_PORT
  try {
    const line = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), '../../.env'), 'utf8')
      .split('\n')
      .find((l) => l.startsWith('APP_PORT='))
    // Chỉ bốc đúng một khoá — `.env` còn chứa API key thật.
    const value = line?.slice('APP_PORT='.length).trim().replace(/^['"]|['"]$/g, '')
    if (value) return value
  } catch {
    // Không có `.env` là chuyện bình thường (CI truyền qua môi trường).
  }
  return '8080'
}

const API = process.env.P118_API ?? `http://127.0.0.1:${appPort()}/api/v1`
const DB = 'p118_db'
const PASSWORD = 'MatKhauBrowser!2030'
const STAMP = String(Date.now()).slice(-9)
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const RESULTS = []

/**
 * Dừng sau một mục, để chạy mutation không phải đợi cả bộ.
 *
 * Các mục phụ thuộc nhau theo thứ tự chạy (quick-action cần tài khoản đã được
 * duyệt liên kết), nên đây là "dừng sau", không phải "chỉ chạy".
 *   P118_STOP_AFTER=auth|link|admin|quick|clarify|happy|idor
 */
const STOP_AFTER = process.env.P118_STOP_AFTER ?? ''

class SetupError extends Error {}

/** Kết thúc sớm nếu người chạy chỉ cần tới mục này. */
function stopHere(section) {
  if (STOP_AFTER !== section) return false
  console.log(`  (dừng sau mục "${section}" theo P118_STOP_AFTER)`)
  return true
}

function sql(query, { expectRows = null } = {}) {
  let out
  try {
    out = execFileSync('docker', [
      'exec', 'p118_postgres', 'psql', '-U', 'p118', '-d', DB,
      '-q', '-v', 'ON_ERROR_STOP=1', '-tAc', query,
    ], { encoding: 'utf8', timeout: 60000 })
  } catch (e) {
    throw new SetupError(`SQL thất bại: ${String(e.stderr || e.message).trim().split('\n').pop().slice(0, 160)}`)
  }
  const rows = out.trim().split('\n').filter(Boolean)
  if (expectRows !== null && rows.length !== expectRows) {
    throw new SetupError(`SQL trả ${rows.length} row, cần ${expectRows}`)
  }
  return rows
}

const mask = (v) => (v && v.length > 8 ? `${v.slice(0, 8)}…` : v || '—')

/** PNG 1×1 hợp lệ — đủ qua kiểm kiểu tệp, không mang nội dung thật. */
const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

/**
 * Body multipart cho `POST /verification-records` (hồ sơ căn hộ).
 *
 * Endpoint này thay `POST /auth/resident-link-requests` từ 7a6a884: nó nhận
 * ảnh giấy tờ nên phải là multipart, và dữ liệu khai nằm trong `claimed_data`
 * (JSONB) chứ không còn là cột riêng.
 */
function apartmentForm(apartmentCode, residentialArea, fullName) {
  const form = new FormData()
  form.append('record_type', 'apartment')
  form.append(
    'claimed_data',
    JSON.stringify({
      apartment_code: apartmentCode,
      residential_area: residentialArea,
      full_name: fullName,
    }),
  )
  form.append('files', new Blob([PNG_1X1], { type: 'image/png' }), 'so-hong-canary.png')
  return form
}

function check(name, ok, detail = '') {
  RESULTS.push({ name, ok, detail })
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
}

/* ------------------------------------------------------------------ */
/* Thao tác UI                                                         */
/* ------------------------------------------------------------------ */

async function registerViaUi(page, username) {
  await page.goto(`${APP}/register`)
  await page.fill('#reg-username', username)
  await page.fill('#reg-email', `${username}@example.test`)
  await page.fill('#reg-password', PASSWORD)
  await page.fill('#reg-confirm', PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForURL((u) => !u.pathname.startsWith('/register'), { timeout: 40000 })
}

async function loginViaUi(page, username, password = PASSWORD) {
  await page.goto(`${APP}/login`)
  await page.fill('#login-username', username)
  await page.fill('#login-password', password)
  await page.click('button[type="submit"]')
}

const TERMINAL_LABELS = new Set([
  'Hoàn thành', 'Không thành công', 'Đã huỷ', 'Đã trả lời',
  'Chưa hiểu được yêu cầu', 'Yêu cầu chưa hợp lệ', 'Không thực hiện được',
])

async function statusLabel(page) {
  return (await page.locator('header p.text-sm.text-gray-500').first().innerText().catch(() => '')).trim()
}

async function waitForApprovalCard(page, workflowId, timeoutMs = 240000) {
  // Workspace hiện ĐÚNG MỘT hành trình đang sống, nên thẻ chờ không cần lọc
  // theo workflow — `[data-pending-card="approval"]` là đủ và không lệ thuộc
  // vào bố cục. `cardFor` bám vào `ChatWorkflowCard`, markup của bề mặt chat
  // CŨ mà giờ chỉ `HomePage` còn dùng.
  const scope = page.locator('[data-pending-card="approval"]')
  try {
    await scope.first().waitFor({ timeout: timeoutMs })
    return true
  } catch {
    console.log(`  !! không thấy card duyệt; nhãn="${await cardLabel(page, workflowId)}"`)
    return false
  }
}

/** Số dịch vụ bị khoá — chỉ đếm trong khu "Dịch vụ". */
/**
 * Số năng lực đang bị khoá vì chưa liên kết hồ sơ cư dân.
 *
 * Workspace không có `<section>` tiêu đề "Dịch vụ" như màn Home cũ; danh sách
 * năng lực là `ul.seq`, và mục bị khoá là hàng `button[disabled]`.
 */
async function lockedCapabilities(page) {
  await page.locator('ul.seq').first().waitFor({ timeout: 20000 }).catch(() => {})
  return page.locator('ul.seq > li button[aria-pressed][disabled]').count()
}


/* ------------------------------------------------------------------ */
/* Hội thoại — luồng mới: KHÔNG điều hướng sau khi gửi                  */
/* ------------------------------------------------------------------ */

/** Mọi workflow đang có thẻ trong hội thoại. */
/**
 * Id của các workflow ĐANG CÓ, đọc từ trang Lịch sử.
 *
 * Hội thoại trong workspace không còn thẻ workflow: canvas hiển thị các chặng
 * của MỘT hành trình, còn danh sách yêu cầu nằm ở `/workflows`. Đọc từ đó là
 * đọc đúng nguồn, thay vì tìm một thẻ không còn tồn tại.
 *
 * Mở trong tab riêng để không cướp mất trang mà phép kiểm đang dùng.
 */
async function cardWorkflowIds(page) {
  // Token nằm ở `sessionStorage`, và sessionStorage KHÔNG chia sẻ giữa các tab
  // — kể cả cùng một browser context. Tab mới mở ra là một phiên chưa đăng
  // nhập, `/workflows` đá về `/login`, và hàm này trả mảng rỗng mãi mãi. Hệ
  // quả: `waitForNewCard` chờ đủ 120 giây rồi trả `''`, nên mọi phép kiểm dựa
  // vào nó báo "không thấy workflow" trong khi workflow đã nằm trong database.
  //
  // Chép token sang trước khi điều hướng. Vẫn dùng tab riêng để không phá
  // trạng thái trang đang mở — 2e kiểm chính URL của nó.
  const token = await page.evaluate(() => sessionStorage.getItem('p118.access_token'))
  const tab = await page.context().newPage()
  try {
    await tab.goto(`${APP}/login`)
    if (token) {
      await tab.evaluate((t) => sessionStorage.setItem('p118.access_token', t), token)
    }
    await tab.goto(`${APP}/workflows`)
    await tab.waitForTimeout(2500)
    const hrefs = await tab.locator('a[href^="/workflow/"]').evaluateAll(
      (els) => els.map((e) => e.getAttribute('href') ?? ''),
    )
    return hrefs.map((href) => href.split('/workflow/')[1] ?? '').filter(Boolean)
  } finally {
    await tab.close()
  }
}

/**
 * Chờ một thẻ MỚI xuất hiện so với ảnh chụp trước đó, rồi trả id của nó.
 *
 * Không dùng "thẻ cuối cùng": sau khi màn hình dựng lại nhiều yêu cầu từ
 * workflow, thẻ cuối cùng có thể là một việc khác hoàn toàn — và mọi phép chờ
 * sau đó rơi vào nhầm workflow. So với ảnh chụp thì không có chỗ cho nhầm lẫn.
 */
async function waitForNewCard(page, before, timeoutMs = 120000) {
  const known = new Set(before)
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const fresh = (await cardWorkflowIds(page)).filter((id) => !known.has(id))
    if (fresh.length > 0) return fresh[fresh.length - 1]
    await page.waitForTimeout(700)
  }
  return ''
}

/** Gõ mục tiêu vào composer rồi gửi. Trả id workflow của thẻ vừa xuất hiện. */
async function sendGoal(page, goal) {
  // `/` redirect sang `/workspace`; đi thẳng để không phụ thuộc vào redirect.
  await page.goto(`${APP}/workspace`)
  await page.locator('#ws-composer').waitFor({ timeout: 20000 })
  // Chờ màn hình dựng lại xong TRƯỚC khi chụp ảnh: chụp giữa chừng thì một
  // thẻ cũ vừa hiện ra sẽ bị tính là thẻ mới.
  await page.waitForTimeout(3000)
  const before = await cardWorkflowIds(page)
  await page.locator('#ws-composer').fill(goal)
  await page.locator('.console-run').click()
  return waitForNewCard(page, before)
}

/**
 * Thẻ của ĐÚNG một workflow.
 *
 * Trước đây mọi helper dùng `.last()`. Nó đúng khi hội thoại chỉ có một thẻ,
 * và sai ngay khi màn hình dựng lại nhiều yêu cầu sau F5: `.last()` trỏ vào
 * một workflow khác, nên phép chờ và phép bấm rơi vào nhầm việc. Chỉ đích danh
 * theo id thì không còn chỗ cho nhầm lẫn.
 */
function cardFor(page, workflowId) {
  return page.locator(
    `section[aria-label="Tiến trình yêu cầu"]:has(a[href="/workflow/${workflowId}"])`,
  )
}

/** Nhãn trạng thái của một thẻ cụ thể. */
async function cardLabel(page, workflowId) {
  const scope = page.locator('[data-pending-card]').last()
  return (await scope.locator('h3').first().innerText().catch(() => '')).trim()
}

/** Chờ thẻ rời trạng thái đang chạy — dừng cả ở điểm chờ người dùng. */
async function waitForCardSettled(page, workflowId, timeoutMs = 240000) {
  const started = Date.now()
  let last = ''
  while (Date.now() - started < timeoutMs) {
    last = await cardLabel(page, workflowId)
    if (last && !['Đang chuẩn bị', 'Đang thực hiện'].includes(last)) return last
    await page.waitForTimeout(1200)
  }
  return last || '(hết giờ chờ)'
}

/** Chờ thẻ KẾT THÚC hẳn.
 *
 * Khác `waitForCardSettled`: "Chờ bạn xác nhận" cũng là một điểm dừng, nên hàm
 * kia trả về ngay ở đó. Sau khi bấm duyệt, chính nhãn đó còn nằm trên màn hình
 * thêm một nhịp poll — đọc lúc ấy sẽ kết luận nhầm là quyết định không có tác
 * dụng, trong khi PostgreSQL đã ghi xong khoản thu. */
async function waitForCardTerminal(page, workflowId, timeoutMs = 240000) {
  const started = Date.now()
  let last = ''
  while (Date.now() - started < timeoutMs) {
    last = await cardLabel(page, workflowId)
    if (TERMINAL_LABELS.has(last)) return last
    await page.waitForTimeout(1200)
  }
  return last || '(hết giờ chờ)'
}

function clarificationSentence(answers) {
  const parts = []
  if (answers.project_name) parts.push(`dự án ${answers.project_name}`)
  if (answers.viewing_date) parts.push(`ngày tham quan ${answers.viewing_date}`)
  if (answers.viewing_time) parts.push(`giờ tham quan ${answers.viewing_time}`)
  if (answers.plate_number) parts.push(`biển số xe ${answers.plate_number}`)
  if (answers.vehicle_type) {
    parts.push(`loại xe ${answers.vehicle_type === 'car' ? 'ô tô' : 'xe máy'}`)
  }
  if (answers.booking_date) parts.push(`ngày đặt chỗ ${answers.booking_date}`)
  if (answers.parking_zone) {
    parts.push(`khu vực đỗ xe ${answers.parking_zone === 'ZONE_A' ? 'Khu A' : 'Khu B'}`)
  }
  return parts.length > 0
    ? `${parts.join(', ')}.`
    : Object.values(answers).map(String).join(', ')
}

/** Trả lời tự nhiên NGAY TRONG hội thoại rồi chờ thẻ chuyển sang workflow con. */
async function answerInChat(page, parentId, answers) {
  const reply = page.locator('#ws-composer')
  await reply.waitFor({ state: 'visible', timeout: 60000 })
  await reply.fill(clarificationSentence(answers))
  const before = await cardWorkflowIds(page)
  await page.locator('.console-run').click()
  // Thẻ cha được thay bằng thẻ con TẠI CHỖ, nên "thẻ mới" ở đây là workflow con.
  const child = await waitForNewCard(page, before)
  return child || parentId
}

/** Mở một workflow đã lưu và tiếp tục nó từ trang chi tiết. */
async function answerOnDetail(page, parentId, answers) {
  await page.goto(`${APP}/workflow/${parentId}`)
  const reply = page.locator('#clarification-reply')
  await reply.waitFor({ timeout: 60000 })
  await reply.fill(clarificationSentence(answers))
  await reply.locator('xpath=ancestor::form').locator('button[type="submit"]').click()
  await page.waitForURL((url) => {
    const path = url.pathname
    return path.startsWith('/workflow/') && !path.endsWith(parentId)
  }, { timeout: 120000 })
  return new URL(page.url()).pathname.split('/workflow/')[1]
}

/** Chờ composer duy nhất chuyển sang chế độ trả lời workflow hiện tại. */
async function waitForChatClarification(page, timeoutMs = 240000) {
  try {
    // Dấu hiệu "P-118 đang chờ mình" giờ là thẻ việc ở cột phải, không phải
    // nút Gửi đổi nhãn.
    await page.locator('#pending-title').waitFor({ timeout: timeoutMs })
    await page.locator('#ws-composer').waitFor({ state: 'visible', timeout: timeoutMs })
    return true
  } catch {
    return false
  }
}

/**
 * Project name của stack ĐANG CHẠY, đọc từ nhãn của container.
 *
 * Không có nó, `docker compose` lấy project name từ tên thư mục. Chạy harness
 * từ một worktree khác nghĩa là một project KHÁC: compose dựng stack thứ hai
 * với volume trống, database rỗng, và mọi con số đo được sau đó là số của một
 * hệ thống không ai đang dùng. Thực tế đã xảy ra — nó tạo
 * `p118-ui-redesign_postgres_data` rồi mới dừng vì trùng tên container.
 *
 * Đặt `P118_COMPOSE_PROJECT` để ép, ngược lại hỏi Docker.
 */
const COMPOSE_PROJECT = (() => {
  if (process.env.P118_COMPOSE_PROJECT) return process.env.P118_COMPOSE_PROJECT
  try {
    return execFileSync('docker',
      ['inspect', 'p118_backend', '--format', '{{index .Config.Labels "com.docker.compose.project"}}'],
      { encoding: 'utf8', timeout: 30000 }).trim()
  } catch {
    return null
  }
})()

function compose(args, { override = null } = {}) {
  if (!COMPOSE_PROJECT) {
    throw new SetupError(
      'Không tìm thấy stack đang chạy (container p118_backend). Chạy `sh scripts/stack_up.sh` trước, '
      + 'hoặc đặt P118_COMPOSE_PROJECT nếu stack mang tên project khác.',
    )
  }
  const files = ['-p', COMPOSE_PROJECT, '-f', 'docker-compose.yml']
  if (override) files.push('-f', override)
  return execFileSync('docker', ['compose', ...files, ...args],
    { cwd: REPO, encoding: 'utf8', timeout: 300000 })
}

async function waitReady(expected, tries = 60) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(`${API.replace('/api/v1', '')}/ready`)
      if (r.status === expected) return true
    } catch { /* backend đang khởi động lại */ }
    await new Promise((r) => setTimeout(r, 2000))
  }
  return false
}

/* ------------------------------------------------------------------ */

async function main() {
  const userA = `br_a_${STAMP}`
  const userB = `br_b_${STAMP}`
  const userR = `br_r_${STAMP}`
  const adminU = `br_adm_${STAMP}`
  const apartment = `BR-${STAMP.slice(-4)}`
  const area = 'Vinhomes Ocean Park'

  // Ngày đặt chỗ phải khác nhau giữa các lần chạy: khu đỗ xe có sức chứa, dùng
  // lại một ngày cố định thì sau vài lượt provider trả NO_AVAILABILITY và
  // happy path hỏng vì lý do không liên quan gì tới thứ đang kiểm.
  const dateOf = (off) => sql(`SELECT (CURRENT_DATE + ${500 + (Number(STAMP) % 900) + off})::text`)[0]
  const [date1, date2, date3, date4] = [dateOf(0), dateOf(1), dateOf(2), dateOf(3)]
  console.log(`  canary: căn hộ ${apartment} · ngày ${date1}/${date2}/${date3}/${date4}`)

  const browser = await chromium.launch()
  const ctxA = await browser.newContext()
  const pageA = await ctxA.newPage()
  const jsErrors = []
  pageA.on('pageerror', (e) => jsErrors.push(String(e).slice(0, 120)))

  /* ============ 1. Auth message ============ */

  await registerViaUi(pageA, userA)
  await pageA.goto(`${APP}/login`)
  await pageA.evaluate(() => sessionStorage.clear())
  await loginViaUi(pageA, userA, 'SaiMatKhauHoanToan!9')
  await pageA.waitForTimeout(2500)
  const wrongText = await pageA.locator('body').innerText()
  check('1a. Sai mật khẩu hiện đúng câu về tài khoản',
    /Tên đăng nhập hoặc mật khẩu không đúng/.test(wrongText), 'ở lại trang đăng nhập')
  check('1b. KHÔNG hiện "phiên đăng nhập đã hết hạn" khi login sai',
    !/Phiên đăng nhập đã hết hạn/.test(wrongText))

  await loginViaUi(pageA, userA)
  await pageA.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 40000 })
  check('1c. Đăng nhập đúng thì vào được app', !pageA.url().includes('/login'))

  // 401 trên request ĐÃ đăng nhập: token rác + tải lại.
  const expired = await ctxA.newPage()
  await expired.goto(`${APP}/login`)
  await expired.evaluate(() => sessionStorage.setItem('p118.access_token', 'token.het.han'))
  await expired.goto(`${APP}/workflows`)
  await expired.waitForTimeout(3000)
  const leftover = await expired.evaluate(() => sessionStorage.getItem('p118.access_token'))
  check('1d. 401 ở request đã đăng nhập thì xoá phiên và về đăng nhập',
    leftover === null && expired.url().includes('/login'),
    `token còn=${leftover !== null}`)
  await expired.close()

  if (stopHere('auth')) return finish(browser)

  /* ============ 3. Customer resident-link request ============ */
  // Chạy trước mục 2 vì mục 2 cần biết dịch vụ nào đang khoá.

  await pageA.goto(`${APP}/`)
  await pageA.waitForTimeout(2500)
  const lockedBefore = await lockedCapabilities(pageA)
  check('3a. Chưa liên kết: dịch vụ cư dân bị khoá', lockedBefore > 0, `khoá=${lockedBefore}`)

  // Đi ĐÚNG đường người dùng đi: `/apartment-link` giờ chỉ là CỬA VÀO — nó cho
  // biết đang ở bước nào rồi dẫn sang cổng của đơn vị xác thực (`/verify`), nơi
  // đặt form nộp. Ranh giới đó phản ánh thực tế: xác minh căn hộ không nằm
  // trong 10 tool của Agent, một đơn vị độc lập mới quyết định.
  //
  // Harness cũ `goto('/apartment-link')` rồi đợi thẳng `#link-apartment`, nên
  // nó treo 20 giây ở một trang không còn form — và cả bộ dừng ngay mục 3.
  // Bấm nút thay vì `goto('/verify')`: nếu nút gãy thì đó là hỏng thật, còn
  // `goto` sẽ che mất.
  await pageA.goto(`${APP}/apartment-link`)
  await pageA.getByRole('link', { name: /xác thực với đơn vị|xem hồ sơ đã gửi/i }).click()
  await pageA.waitForURL(/\/verify/, { timeout: 20000 })
  // Màn chuyển giao có sàn hiển thị ~900ms trước khi nhường chỗ cho form.
  await pageA.locator('#link-apartment').waitFor({ timeout: 20000 })
  const linkFormIds = await pageA.locator('form input, form select').evaluateAll((els) => els.map((e) => e.id))
  check('3b. Form chỉ hỏi thông tin người dùng biết',
    !linkFormIds.some((id) => /user|resident-id|verif|status/i.test(id)),
    `ô=[${linkFormIds.join(',')}]`)

  await pageA.fill('#link-apartment', apartment)
  await pageA.fill('#link-area', area)
  await pageA.fill('#link-name', 'Nguyen Van Browser')
  // Liên kết căn hộ giờ BẮT BUỘC ảnh giấy tờ (thêm ở 7a6a884, phần hồ sơ xác
  // thực). Không đính kèm thì nút gửi disabled và phép bấm treo 30 giây — một
  // thất bại của harness, không phải của sản phẩm.
  await pageA.locator('#link-files').setInputFiles({
    name: 'so-hong-canary.png',
    mimeType: 'image/png',
    // PNG 1×1 hợp lệ — đủ để qua kiểm kiểu tệp, không mang nội dung thật.
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
      'base64',
    ),
  })
  await pageA.locator('form button[type="submit"]').click()
  await pageA.waitForTimeout(3000)

  const uidA = sql(`SELECT id FROM users WHERE username = '${userA}'`, { expectRows: 1 })[0]
  const pending1 = sql(
    `SELECT count(*) FROM verification_records
     WHERE applicant_user_id = '${uidA}' AND record_type = 'apartment' AND status = 'PENDING'`)[0]
  check('3c. Gửi tạo đúng một yêu cầu PENDING', pending1 === '1', `PENDING=${pending1}`)

  await pageA.reload()
  await pageA.waitForTimeout(2500)
  const afterReload = await pageA.locator('body').innerText()
  // "ban quản lý" → "đơn vị xác thực": cùng một hồ sơ mà hai trang gọi bên
  // duyệt bằng hai tên khác nhau, đã thống nhất lại.
  check('3d. Reload vẫn thấy trạng thái chờ duyệt', /chờ đơn vị xác thực duyệt/i.test(afterReload))

  // Gửi lần hai: form đã bị ẩn khi đang PENDING, nên thử thẳng qua API bằng
  // token của chính trang — kiểm ràng buộc backend chứ không kiểm việc ẩn nút.
  const tokenA = await pageA.evaluate(() => sessionStorage.getItem('p118.access_token'))
  const dup = await fetch(`${API}/verification-records`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${tokenA}` },
    // multipart, KHÔNG phải JSON: endpoint nhận ảnh giấy tờ. Không tự đặt
    // `Content-Type` — boundary do FormData sinh ra, ghi đè là hỏng parse.
    body: apartmentForm(apartment, area, 'Nguyen Van Browser'),
  })
  const pending2 = sql(
    `SELECT count(*) FROM verification_records
     WHERE applicant_user_id = '${uidA}' AND record_type = 'apartment' AND status = 'PENDING'`)[0]
  check('3e. Gửi lần hai không tạo yêu cầu trùng', dup.status === 409 && pending2 === '1',
    `http=${dup.status} PENDING=${pending2}`)

  if (stopHere('link')) return finish(browser)

  /* ============ 4. Admin approval ============ */

  const ctxAdm = await browser.newContext()
  const pageAdm = await ctxAdm.newPage()
  await registerViaUi(pageAdm, adminU)
  sql(`UPDATE users SET role = 'admin' WHERE username = '${adminU}'`)
  await loginViaUi(pageAdm, adminU)
  await pageAdm.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 40000 })

  // Cổng duyệt đã chuyển từ `/admin/link-requests` sang `/review` (7a6a884):
  // việc duyệt hồ sơ xác thực thuộc về bên thứ ba, không phải admin nội bộ.
  // Trang mở mặc định ở tab "Căn hộ".
  await pageAdm.goto(`${APP}/review`)
  await pageAdm.waitForTimeout(3000)
  const queueText = await pageAdm.locator('body').innerText()
  // KHÔNG đòi hàng chờ hiện `userA`: trang duyệt cố ý không phơi tên đăng nhập
  // của người nộp đơn — 4c ngay dưới đây canh đúng nguyên tắc đó. Đòi cả hai
  // là tự mâu thuẫn.
  check('4a. Người duyệt thấy yêu cầu thật trong hàng chờ',
    queueText.includes(apartment), `căn hộ ${apartment}`)
  check('4b. Hàng chờ không có ô nhập UUID/mã cư dân',
    (await pageAdm.locator('input[id="user-id"], input[id="resident-id"]').count()) === 0)
  // 4c KHÔNG còn là "tên đã mask".
  //
  // Ở hàng chờ admin nội bộ cũ, tên người nộp đơn bị che. Cổng /review là một
  // BÊN THỨ BA và công việc của họ là đối chiếu tên khai với hồ sơ căn hộ —
  // che đi thì không duyệt được gì. Nên tên KHAI được hiện, có chủ ý.
  //
  // Thứ vẫn phải giấu là `owner_name` trong registry: provider chỉ trả
  // `ownership_match: bool`. Đó mới là ranh giới thật, và đây là chỗ canh nó.
  const reviewToken = await pageAdm.evaluate(() => sessionStorage.getItem('p118.access_token'))
  const queueJson = await (await fetch(`${API}/verification-records?record_type=apartment&status=PENDING`, {
    headers: { Authorization: `Bearer ${reviewToken}` },
  })).text()
  check('4c. Người duyệt nhận ownership_match, KHÔNG nhận owner_name',
    !queueJson.includes('owner_name') && queueJson.includes('ownership_match'),
    `owner_name=${queueJson.includes('owner_name')}`)

  const row = pageAdm.locator('li', { hasText: apartment }).first()
  await row.locator('button', { hasText: 'Duyệt' }).click()
  await pageAdm.waitForTimeout(4000)

  const link = sql(`SELECT verification_status FROM user_resident_links WHERE user_id = '${uidA}'`)
  const residentRow = sql(
    `SELECT r.apartment_code FROM user_resident_links l JOIN residents r ON r.resident_id = l.resident_id
     WHERE l.user_id = '${uidA}'`)
  check('4d. Duyệt tạo mapping VERIFIED trong một transaction',
    link[0] === 'VERIFIED' && residentRow[0] === apartment,
    `status=${link[0]} căn hộ=${residentRow[0]}`)

  await loginViaUi(pageA, userA)
  await pageA.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 40000 })
  await pageA.goto(`${APP}/`)
  await pageA.waitForTimeout(3000)
  const lockedAfter = await lockedCapabilities(pageA)

  // Mã căn hộ đọc ở `/profile`, không phải `/`.
  //
  // `/` giờ chuyển hướng sang `/workspace` — không gian làm việc không hiện
  // thông tin hồ sơ, và đó là chủ ý (`HomeRedirect` trong App.tsx ghi rõ, kèm
  // ghi chú rằng harness cần cập nhật). Đòi mã căn hộ ở đây là đòi một màn
  // hình khác phải làm việc của trang hồ sơ.
  await pageA.goto(`${APP}/profile`)
  await pageA.waitForTimeout(3000)
  const profileAfter = await pageA.locator('body').innerText()
  check('4e. Customer thấy căn hộ ở hồ sơ và dịch vụ được mở',
    profileAfter.includes(apartment) && lockedAfter === 0,
    `căn hộ hiện=${profileAfter.includes(apartment)} khoá còn=${lockedAfter}`)

  // Customer không tự duyệt được.
  const ctxR = await browser.newContext()
  const pageR = await ctxR.newPage()
  await registerViaUi(pageR, userR)
  const tokenR0 = await pageR.evaluate(() => sessionStorage.getItem('p118.access_token'))
  const rejectCode = `RJ-${STAMP.slice(-4)}`
  await fetch(`${API}/verification-records`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${tokenR0}` },
    body: apartmentForm(rejectCode, area, 'Khach Reject'),
  })
  const uidR = sql(`SELECT id FROM users WHERE username = '${userR}'`, { expectRows: 1 })[0]
  const reqR = sql(
    `SELECT record_id FROM verification_records WHERE applicant_user_id = '${uidR}'`,
    { expectRows: 1 })[0]

  const selfApprove = await fetch(`${API}/verification-records/${reqR}/decide`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tokenR0}` },
    body: JSON.stringify({ decision: 'approve' }),
  })
  check('4f. Customer không tự duyệt được yêu cầu của mình',
    [401, 403].includes(selfApprove.status)
    && sql(`SELECT count(*) FROM user_resident_links WHERE user_id = '${uidR}'`)[0] === '0',
    `http=${selfApprove.status}`)

  await pageAdm.goto(`${APP}/review`)
  await pageAdm.waitForTimeout(3000)
  await pageAdm.locator('li', { hasText: rejectCode }).first()
    .locator('button', { hasText: 'Từ chối' }).click()
  // Từ chối BẮT BUỘC lý do: backend trả 422 nếu thiếu, và UI chặn bằng modal.
  // Bỏ qua bước này thì phép bấm không bao giờ thành một quyết định, và 4g đỏ
  // vì lý do không liên quan tới thứ nó đang kiểm.
  //
  // Bám ĐÚNG chữ trên nút xác nhận. Bản trước dùng `/^(Xác nhận|Từ chối)$/` và
  // khớp trúng một nút khác đang disabled, nên phép bấm treo đủ 30 giây rồi
  // giết cả lượt chạy — một thất bại của harness đội lốt lỗi sản phẩm.
  const reasonBox = pageAdm.locator('textarea').last()
  await reasonBox.fill('Ảnh giấy tờ không đọc được')
  await pageAdm.locator('button', { hasText: 'Xác nhận từ chối' }).click()
  await pageAdm.waitForTimeout(4000)
  check('4g. Từ chối: không mở quyền, yêu cầu ghi REJECTED',
    sql(`SELECT status FROM verification_records WHERE applicant_user_id = '${uidR}'`)[0] === 'REJECTED'
    && sql(`SELECT count(*) FROM user_resident_links WHERE user_id = '${uidR}'`)[0] === '0')

  if (stopHere('admin')) return finish(browser)

  /* ============ 2. Quick action là form, không phải prompt chip ============ */

  const startPosts = []
  pageA.on('request', (r) => {
    if (r.url().includes('/workflows/demo/start') && r.method() === 'POST') {
      startPosts.push(r.postData() ?? '')
    }
  })

  // Danh sách dịch vụ giờ là `ul.seq` trong workspace, KHÔNG còn
  // `<section><h2>Dịch vụ</h2>` của màn Home cũ; field xổ ngay trong từng dòng
  // thay vì gom vào một `<form data-quick-action-form>` với `<fieldset>` mỗi
  // dịch vụ. Các phép kiểm bên dưới giữ nguyên Ý NGHĨA, chỉ đổi cách chỉ tới
  // phần tử — riêng 2e đổi cả kỳ vọng vì `/` nay chuyển hướng sang `/workspace`.
  await pageA.goto(`${APP}/workspace`)
  await pageA.waitForTimeout(2500)
  const cards = pageA.locator('ul.seq > li button[aria-pressed]:not([disabled])')
  await cards.nth(0).click()
  await pageA.waitForTimeout(1200)
  check('2a. Click một quick action KHÔNG tạo workflow', startPosts.length === 0,
    `POST /start=${startPosts.length}`)

  const serviceList = pageA.locator('ul.seq')
  check('2b. Quick action mở form chuẩn bị ngay trên trang',
    (await serviceList.locator('input, select').count()) >= 3)
  check('2c. Ô prompt vẫn độc lập và không bị quick action điền hộ',
    (await pageA.locator('#ws-composer').inputValue()) === '')

  // Chọn thêm một dịch vụ phải GIỮ dịch vụ đầu và xổ thêm nhóm field, không
  // thay form cũ như single-select. Sau đó bỏ mục thứ hai để phần happy path
  // bên dưới chỉ cần điền một dịch vụ.
  await cards.nth(1).click()
  await pageA.waitForTimeout(800)
  check('2c1. Có thể chọn nhiều dịch vụ và form xổ đủ từng nhóm',
    (await serviceList.locator('button[aria-pressed="true"]').count()) === 2
    && (await serviceList.locator('> li:has(input, select)').count()) === 2)
  await cards.nth(1).click()
  await pageA.waitForTimeout(800)
  check('2c2. Bấm lại chỉ bỏ đúng dịch vụ đó',
    (await serviceList.locator('button[aria-pressed="true"]').count()) === 1
    && (await serviceList.locator('> li:has(input, select)').count()) === 1)
  check('2c3. Dịch vụ tìm gợi ý bất động sản đã được gỡ',
    !(await serviceList.innerText()).includes('Tìm gợi ý bất động sản'))

  // Id ổn định do `serviceForms.ts` sinh: `f-<khoá field>`. Bám theo id thay vì
  // thứ tự thẻ — thêm một field mới sẽ không lặng lẽ làm phép điền lệch ô.
  const projectSelect = pageA.locator('#f-project')
  for (let i = 0; i < 40; i++) {
    if (await projectSelect.locator('option').count() > 1) break
    await pageA.waitForTimeout(500)
  }
  await projectSelect.selectOption({ index: 1 })
  // `date` là field DÙNG CHUNG: nó nằm ngoài dòng dịch vụ và mang tiền tố id
  // KHÁC — `shared-<khoá>` chứ không phải `f-<khoá>`. Hai tiền tố phản ánh hai
  // chỗ dựng khác nhau (`ServiceLauncher` vs `InlineServiceForm`).
  await pageA.locator('#shared-date').fill(date1)
  await pageA.locator('#f-time').selectOption({ index: 1 })
  // `needs_shuttle` là field BẮT BUỘC. Bỏ trống thì nút "Thực hiện" vẫn sáng
  // nhưng execute bị chặn kèm thông báo "Còn thiếu: Xe đưa đón" — không có
  // request nào đi, và 2d đỏ với `POST=0` trông y như lỗi backend.
  // Chọn "Tôi tự đi" để không kéo theo nhóm field địa điểm đón.
  await pageA.locator('#f-needs_shuttle').selectOption('false')

  const before = Number(sql(`SELECT count(*) FROM workflows WHERE owner_user_id = '${uidA}'`)[0])
  const cardsBeforeSend = await cardWorkflowIds(pageA)
  await pageA.locator('button', { hasText: 'Thực hiện' }).first().click()
  const wfQuick = await waitForNewCard(pageA, cardsBeforeSend)
  const after = Number(sql(`SELECT count(*) FROM workflows WHERE owner_user_id = '${uidA}'`)[0])
  check('2d. Chỉ khi gửi form mới tạo ĐÚNG một workflow',
    startPosts.length === 1 && after === before + 1, `POST=${startPosts.length} workflow ${before}→${after}`)

  // Luồng cũ đẩy người dùng sang /workflow/{id} ngay sau khi gửi. Cuộc hội
  // thoại vì thế đứt làm hai màn hình.
  check('2e. Gửi form xong KHÔNG bị đẩy sang trang khác',
    new URL(pageA.url()).pathname === '/workspace', `url=${new URL(pageA.url()).pathname}`)
  // Goal do form dựng mang TÊN DỰ ÁN và ngày, không phải tên dịch vụ chung
  // chung: "Đặt lịch tham quan Vinhomes Ocean Park ngày …". Bản trước tìm
  // "Đặt lịch tham quan dự án" — nhãn trong danh sách năng lực, không bao giờ
  // là câu được gửi đi.
  const conversation = await pageA.locator('body').innerText()
  check('2f. Goal do form chuẩn bị xuất hiện trong hội thoại',
    /Đặt lịch tham quan .*ngày \d{4}-\d{2}-\d{2}/.test(conversation),
    /Đặt lịch tham quan .*ngày \d{4}-\d{2}-\d{2}/.test(conversation)
      ? ''
      : (conversation.includes('Đặt lịch tham quan') ? 'có chữ nhưng sai dạng' : 'không thấy goal'))
  // Workspace không dựng `section[aria-label="Tiến trình yêu cầu"]` như hội
  // thoại cũ; tiến trình nằm ở canvas hành trình.
  check('2g. Hành trình xuất hiện ngay sau khi gửi',
    Boolean(wfQuick) && (await pageA.locator('[aria-label="Chi tiết hành trình"]').count()) >= 1,
    `workflow=${Boolean(wfQuick)}`)

  const body = JSON.parse(startPosts[0] || '{}')
  const forbidden = ['tasks', 'task_plan', 'tools', 'resident_id', 'account_state', 'owner_user_id',
    'existing_context', 'session_id', 'approve_mock_payment']
  const smuggled = forbidden.filter((k) => k in body)
  check('2h. Body chỉ mang goal, không mang gì quyết định quyền',
    smuggled.length === 0 && Object.keys(body).every((k) => ['goal', 'project_name'].includes(k)),
    `key=[${Object.keys(body).join(',')}]`)

  if (stopHere('quick')) return finish(browser)

  /* ============ 6. Happy path liên hoàn ============ */

  // Prompt lane độc lập với quick form. Model tự nhận diện chuỗi dịch vụ và
  // hỏi lại bằng hội thoại; không render lại form quick action.
  const wfCompound = await sendGoal(
    pageA,
    'Đặt lịch tham quan dự án, đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí.',
  )
  const hasForm = await waitForChatClarification(pageA)
  check('6a. Prompt thiếu dữ liệu được hỏi lại bằng hội thoại', hasForm)
  check('6b. Prompt lane không render form field cấu trúc',
    (await pageA.locator('form[data-quick-action-form]').count()) === 0
    && (await pageA.locator('[id^="clarify-"]').count()) === 0)
  check('6b0. Home chỉ có đúng một ô chat',
    (await pageA.locator('textarea').count()) === 1
    && (await pageA.locator('#ws-composer').count()) === 1)

  // Câu hỏi phụ không phải một giá trị field: phải được trả lời trên cùng
  // workflow, không 422 và không consume lượt clarification.
  const cardsBeforeQuestion = await cardWorkflowIds(pageA)
  const lookupReply = pageA.locator('#ws-composer')
  await lookupReply.fill('Có những dự án nào?')
  // Ghi lại MỌI request đi ra trong lúc này. Nếu câu hỏi phụ lại rơi vào một
  // workflow khác (hoặc mở workflow mới), đó là một phát hiện về sản phẩm —
  // và phải đọc được, thay vì lẫn vào một lần treo 30 giây không giải thích.
  const lookupCalls = []
  const recordCall = (r) => {
    if (r.url().includes('/workflows/demo/')) lookupCalls.push(`${r.method()} ${r.url().split('/api/v1')[1]}`)
  }
  pageA.on('request', recordCall)
  const lookupResponsePromise = pageA.waitForResponse(
    (response) => response.url().includes(`/workflows/demo/${wfCompound}/continue`)
      && response.request().method() === 'POST',
  ).catch(() => null)
  // Nút gửi của workspace KHÔNG tên "Gửi": ở chế độ chọn dịch vụ nó là
  // "Thực hiện", ở chế độ hành trình là "Gửi cho P-118" (xem `CommandRail`).
  // `getByRole(name: 'Gửi', exact: true)` không khớp cái nào, nên phép bấm
  // không xảy ra và `waitForResponse` treo đủ 30 giây rồi ném lỗi ra ngoài
  // `try` — giết cả lượt chạy thay vì đánh đỏ một check.
  //
  // `.console-run` là chính nút đó, và các helper khác trong file này đã dùng
  // nó — bám theo cho nhất quán.
  await pageA.locator('.console-run').click()
  const lookupResponse = await lookupResponsePromise
  pageA.off('request', recordCall)
  const lookupBody = lookupResponse ? await lookupResponse.json().catch(() => ({})) : {}
  check('6b1. Câu hỏi phụ được API chấp nhận', lookupResponse?.status() === 202,
    lookupResponse
      ? `http=${lookupResponse.status()} detail=${String(lookupBody.detail ?? '').slice(0, 120)}`
      : `không có POST /continue tới ${wfCompound.slice(0, 8)} — đã gọi: [${lookupCalls.join(', ') || 'không gì'}]`)
  if (lookupResponse?.status() !== 202) return finish(browser)
  // Câu trả lời là một bubble hội thoại độc lập, không nhét ngược vào card
  // tiến trình. Đây chính là ranh giới giúp card chỉ biểu diễn trạng thái.
  // `[data-turn="agent"]` thay cho `div.flex.flex-col.items-start > p`: selector
  // cũ bám vào class Tailwind và đã chết khi bố cục hội thoại đổi — nó treo 30
  // giây rồi giết cả lượt chạy, trong khi câu trả lời vẫn hiện đúng trên màn
  // hình. Xem `ConversationStream`.
  await pageA.locator('[data-turn="agent"]', { hasText: 'Vinhomes Ocean Park' })
    .last().waitFor({ timeout: 30000 })
  check('6b2. Hỏi danh sách dự án được trả lời mà không tạo workflow mới',
    JSON.stringify(await cardWorkflowIds(pageA)) === JSON.stringify(cardsBeforeQuestion)
    && (await pageA.locator('#ws-composer').count()) === 1)
  const userHistory = await pageA.locator('[data-turn="user"]').allInnerTexts()
  const assistantHistory = await pageA.locator('[data-turn="agent"]').allInnerTexts()
  check('6b3. Hỏi tiếp không làm mất lịch sử cùng workflow',
    userHistory.some((text) => text.includes('Đặt lịch tham quan dự án'))
    && userHistory.some((text) => text.includes('Có những dự án nào'))
    && assistantHistory.length >= 2,
    `user=${userHistory.length} assistant=${assistantHistory.length}`)
  check('6b4. Response Agent không còn gợi ý dịch vụ tìm bất động sản',
    (await pageA.getByRole('button', { name: 'Tìm gợi ý bất động sản', exact: true }).count()) === 0)

  if (stopHere('clarify')) return finish(browser)

  const wfPay = await answerInChat(pageA, wfCompound, {
    project_name: 'Vinhomes Ocean Park', viewing_date: date1, viewing_time: '10:00',
    plate_number: `51B-${STAMP.slice(-5)}`, vehicle_type: 'car',
    booking_date: date1, parking_zone: 'ZONE_A',
  })
  // Hội thoại giờ ở `/workspace`, không phải `/` — `HomeRedirect` đưa khách
  // hàng sang đó và `/` chỉ còn là chặng chuyển hướng.
  check('6a2. Trả lời xong vẫn ở lại hội thoại, thẻ chuyển sang lượt chạy mới',
    new URL(pageA.url()).pathname === '/workspace' && wfPay !== wfCompound,
    `url=${new URL(pageA.url()).pathname} wf=${mask(wfPay)}`)
  const cardShown = await waitForApprovalCard(pageA, wfPay)
  // `[data-step-list]` thay cho `section[aria-label="Tiến trình yêu cầu"]`:
  // selector cũ là markup của `ChatWorkflowCard` — bề mặt chat CŨ, giờ chỉ còn
  // `HomePage` dùng. Workspace dựng danh sách bước bằng `StepList`.
  const steps = await pageA.locator('[data-step-list] [data-step]').allInnerTexts().catch(() => [])
  check('6c. Hiện đủ bước nghiệp vụ bằng tiếng Việt',
    cardShown && steps.length >= 3 && !steps.some((s) => /register_vehicle|book_parking|pay_fee/.test(s)),
    steps.slice(0, 3).join(' | ').slice(0, 80))

  // Hỏi theo NHÃN thay vì bám vào cỡ chữ (`p.text-2xl`): số tiền vẫn là số
  // tiền kể cả khi thiết kế đổi bậc typography.
  const uiAmount = await pageA.locator('[data-detail="Số tiền"] dd').first().innerText().catch(() => '')
  const dbAmount = sql(
    `SELECT b.amount FROM payment_approvals a JOIN parking_bookings b ON b.booking_id = a.booking_id
     WHERE a.workflow_id = '${wfPay}'::uuid AND a.status = 'AWAITING'`)[0]
  // Hai giá trị RỖNG bằng nhau vẫn là bằng nhau — và phép so ngây thơ ở bản
  // trước báo xanh cho `UI="" DB=undefined`, tức không đọc được gì mà vẫn nói
  // "khớp". Một phép kiểm đạt một cách vô nghĩa còn tệ hơn phép kiểm đỏ: nó
  // khẳng định thứ nó chưa hề nhìn thấy.
  const uiDigits = uiAmount.replace(/\D/g, '')
  const dbDigits = String(dbAmount ?? '').replace(/\D/g, '')
  check('6d. Báo giá khớp booking authoritative',
    uiDigits.length > 0 && dbDigits.length > 0 && uiDigits === dbDigits,
    `UI="${uiAmount}" DB=${dbAmount}`)

  const payBefore = Number(sql('SELECT count(*) FROM payments')[0])
  await cardFor(pageA, wfPay).locator('button', { hasText: 'Xác nhận thanh toán' }).click()
  const okLabel = await waitForCardTerminal(pageA, wfPay)
  const payAfter = Number(sql('SELECT count(*) FROM payments')[0])
  check('6e. Duyệt → Hoàn thành, đúng một payment',
    okLabel === 'Hoàn thành' && payAfter === payBefore + 1, `${okLabel} · payments ${payBefore}→${payAfter}`)

  // Response Agent: P-118 phải NÓI một câu về chính yêu cầu này, ngay trong
  // hội thoại. Trước lượt này chỗ đó chỉ có câu ghép cứng theo stage.
  // Câu trả lời tới sau kết quả vài nhịp (backend sinh ở tác vụ nền).
  await pageA.locator('div.flex.flex-col.items-start p').first()
    .waitFor({ timeout: 30000 }).catch(() => {})
  const bubbles = await pageA.locator('div.flex.flex-col.items-start p').allInnerTexts().catch(() => [])
  const spoken = bubbles.filter((b) => b.trim().length > 20)
  check('6h. P-118 trả lời bằng lời trong hội thoại', spoken.length > 0,
    (spoken.at(-1) ?? '').slice(0, 90))
  const chatLeaks = ['Planner', 'Executor', 'Validator', 'register_vehicle', 'book_parking',
    'pay_fee', 'WAITING_APPROVAL', 'SUCCESS', 'postgresql://', 'workflow_id']
    .filter((t) => spoken.join(' ').includes(t))
  check('6i. Câu trả lời không lộ thuật ngữ nội bộ', chatLeaks.length === 0,
    chatLeaks.length ? `lộ ${chatLeaks.join(',')}` : 'sạch')

  // Reject trên workflow riêng.
  const wfR0 = await sendGoal(pageA, 'Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí.')
  await waitForChatClarification(pageA)
  const wfR = await answerInChat(pageA, wfR0, {
    plate_number: `51C-${STAMP.slice(-5)}`, vehicle_type: 'car',
    booking_date: date2, parking_zone: 'ZONE_B',
  })
  await waitForApprovalCard(pageA, wfR)
  const bookingR = sql(
    `SELECT b.booking_id FROM payment_approvals a JOIN parking_bookings b ON b.booking_id = a.booking_id
     WHERE a.workflow_id = '${wfR}'::uuid`)
  const payBeforeR = Number(sql('SELECT count(*) FROM payments')[0])
  await cardFor(pageA, wfR).locator('button', { hasText: 'Từ chối' }).click()
  const rejLabel = await waitForCardTerminal(pageA, wfR)
  const payAfterR = Number(sql('SELECT count(*) FROM payments')[0])
  check('6f. Từ chối: không thu tiền, chỗ đã giữ vẫn còn',
    payAfterR === payBeforeR
    && sql(`SELECT status FROM workflows WHERE workflow_id = '${wfR}'::uuid`)[0] === 'CANCELLED'
    && sql(`SELECT count(*) FROM parking_bookings WHERE booking_id = '${bookingR[0]}'`)[0] === '1',
    `${rejLabel} · payments ${payBeforeR}→${payAfterR}`)

  // Restart backend tại WAITING_APPROVAL.
  const wfS0 = await sendGoal(pageA, 'Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí.')
  await waitForChatClarification(pageA)
  const wfS = await answerInChat(pageA, wfS0, {
    plate_number: `51D-${STAMP.slice(-5)}`, vehicle_type: 'car',
    booking_date: date3, parking_zone: 'ZONE_A',
  })
  const restartCard = await waitForApprovalCard(pageA, wfS)
  if (!restartCard) {
    check('6g. Restart container: giữ báo giá và hai nút', false, 'không tới được chờ duyệt')
  } else {
    compose(['restart', 'backend'])
    await waitReady(200)
    // Hội thoại là state trong RAM của tab, nên reload xoá nó — đúng như thiết
    // kế. Điều PHẢI sống sót là workflow: mở trang chi tiết và kiểm ở đó.
    await pageA.goto(`${APP}/workflow/${wfS}`)
    await pageA.waitForTimeout(4000)
    const stepsAfter = await pageA.locator('ol li p.font-medium').count()
    const btns = await pageA.locator('button', { hasText: /Xác nhận thanh toán|Từ chối/ }).count()
    const amountAfter = await pageA.locator('p.text-2xl').first().innerText().catch(() => '')
    check('6g. Restart container: giữ báo giá và hai nút',
      stepsAfter >= 3 && btns === 2 && amountAfter.replace(/\D/g, '').length > 0,
      `bước=${stepsAfter} nút=${btns} tiền="${amountAfter}"`)
  }

  /* ============ 8. Reload mở chat mới, workflow vẫn tiếp tục được ============ */

  const wfH0 = await sendGoal(pageA, 'Đăng ký ô tô, đặt chỗ đỗ xe và thanh toán phí.')
  await waitForChatClarification(pageA)

  await pageA.reload()
  await pageA.waitForTimeout(5000)
  check('8a. F5 mở cuộc trò chuyện mới, không tự nhét workflow cũ vào chat',
    (await cardWorkflowIds(pageA)).length === 0
    && (await pageA.locator('#clarification-reply').count()) === 0
    && (await pageA.locator('#ws-composer').count()) === 1
    && new URL(pageA.url()).pathname === '/')

  // Workflow không mất: người dùng chủ động mở lại từ mục Workflows (ở đây
  // dùng URL đã biết để kiểm đúng contract của trang chi tiết).
  const wfH = await answerOnDetail(pageA, wfH0, {
    plate_number: `51H-${STAMP.slice(-5)}`, vehicle_type: 'car',
    booking_date: date4, parking_zone: 'ZONE_A',
  })
  await pageA.locator('text=Cần bạn xác nhận khoản thanh toán').waitFor({ timeout: 240000 })

  await pageA.reload()
  await pageA.waitForTimeout(5000)
  const stepsH = await pageA.locator('ol li').count()
  const btnsH = await pageA.locator('button', { hasText: /Xác nhận thanh toán|Từ chối/ }).count()
  const amountH = await pageA.locator('p.text-2xl').first().innerText().catch(() => '')
  check('8b. Workflow đang dở mở lại vẫn có bước, báo giá và hai nút',
    stepsH >= 3 && btnsH === 2 && amountH.replace(/\D/g, '').length > 0,
    `bước=${stepsH} nút=${btnsH} tiền="${amountH}"`)

  const payBeforeH = Number(sql('SELECT count(*) FROM payments')[0])
  await pageA.locator('button', { hasText: 'Xác nhận thanh toán' }).click()
  for (let i = 0; i < 120; i++) {
    if (sql(`SELECT status FROM workflows WHERE workflow_id = '${wfH}'::uuid`)[0] === 'SUCCESS') break
    await pageA.waitForTimeout(1000)
  }
  const payAfterH = Number(sql('SELECT count(*) FROM payments')[0])
  check('8c. Duyệt → Hoàn thành, đúng một payment',
    sql(`SELECT status FROM workflows WHERE workflow_id = '${wfH}'::uuid`)[0] === 'SUCCESS'
    && payAfterH === payBeforeH + 1,
    `payments ${payBeforeH}→${payAfterH}`)

  // Chờ câu trả lời cho trạng thái MỚI được ghi xong. Đọc lúc còn PENDING sẽ
  // lấy nhầm câu của trạng thái trước.
  for (let i = 0; i < 40; i++) {
    const state = sql(
      `SELECT coalesce(assistant_response_state,'-') FROM workflows WHERE workflow_id = '${wfH}'::uuid`)[0]
    if (state === 'READY' || state === 'FALLBACK') break
    await pageA.waitForTimeout(1000)
  }

  // Câu trả lời phải nằm trong DATABASE, không chỉ trong RAM của tiến trình.
  const storedAnswer = sql(
    `SELECT coalesce(assistant_answer,'(trống)') || ' | ' || coalesce(assistant_response_state,'-')
     || ' | ' || coalesce(assistant_for_status,'-')
     FROM workflows WHERE workflow_id = '${wfH}'::uuid`)[0]
  check('8d. Câu trả lời được ghi xuống PostgreSQL kèm trạng thái',
    !storedAnswer.startsWith('(trống)') && storedAnswer.includes('SUCCESS'),
    storedAnswer.slice(0, 100))

  console.log('  → khởi động lại backend, giữ nguyên tab')
  compose(['restart', 'backend'])
  await waitReady(200)
  await pageA.goto(`${APP}/workflow/${wfH}`)
  await pageA.waitForTimeout(6000)
  const afterRestart = await pageA.locator('body').innerText()
  const answerText = storedAnswer.split(' | ')[0]
  check('8e. Restart backend: workflow VÀ câu trả lời còn nguyên ở trang chi tiết',
    afterRestart.includes(answerText.slice(0, 30)),
    answerText.slice(0, 70))

  // Đăng nhập lại: phiên mới, dữ liệu cũ.
  await pageA.evaluate(() => sessionStorage.clear())
  await loginViaUi(pageA, userA)
  await pageA.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 40000 })
  await pageA.goto(`${APP}/workflows`)
  await pageA.waitForTimeout(6000)
  const afterRelogin = await pageA.locator('body').innerText()
  check('8f. Đăng nhập lại vẫn thấy yêu cầu trong danh sách workflow',
    afterRelogin.includes('Đăng ký ô tô, đặt chỗ đỗ xe'))

  const cardLinks = await pageA.locator('a[href^="/workflow/"]').evaluateAll(
    (els) => els.map((e) => e.getAttribute('href')))
  check('8g. Danh sách không nhân đôi workflow',
    new Set(cardLinks).size === cardLinks.length,
    `thẻ=${cardLinks.length} khác nhau=${new Set(cardLinks).size}`)

  // Workflow cha đã archived không được hiện thành một yêu cầu riêng.
  check('8h. Workflow cha đã bàn giao không hiện lại',
    !cardLinks.some((href) => (href ?? '').includes(wfH0)),
    `cha=${mask(wfH0)}`)

  check('8i. Chỉ vào trang chi tiết khi người dùng chủ động mở workflow',
    new URL(pageA.url()).pathname === '/workflows')

  if (stopHere('happy')) return finish(browser)

  /* ============ 7. IDOR ============ */

  const ctxB = await browser.newContext()
  const pageB = await ctxB.newPage()
  await registerViaUi(pageB, userB)
  await pageB.goto(`${APP}/workflows`)
  await pageB.waitForTimeout(3000)
  const listB = await pageB.locator('body').innerText()
  check('7a. B không thấy workflow của A trong danh sách',
    !listB.includes(wfPay.slice(0, 8)) && !listB.includes(wfS.slice(0, 8)))

  await pageB.goto(`${APP}/workflow/${wfPay}`)
  await pageB.waitForTimeout(3500)
  const detailB = await pageB.locator('body').innerText()
  check('7b. B mở URL của A nhận màn không tìm thấy', /Không tìm thấy yêu cầu/.test(detailB))

  const tokenB = await pageB.evaluate(() => sessionStorage.getItem('p118.access_token'))
  const idor = []
  for (const [method, path, payload] of [
    ['GET', `/workflows/demo/${wfPay}`, null],
    ['POST', `/workflows/demo/${wfPay}/continue`, { fields: { parking_zone: 'ZONE_A' } }],
    ['POST', `/workflows/demo/${wfPay}/payment-decision`, { decision: 'approve' }],
  ]) {
    const r = await fetch(`${API}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tokenB}` },
      body: payload ? JSON.stringify(payload) : undefined,
    })
    idor.push(r.status)
  }
  check('7c. B nhận 404 ở đọc/continue/duyệt của A', idor.every((s) => s === 404), `http=${idor}`)

  const otherRequest = await fetch(`${API}/admin/resident-link-requests`, {
    headers: { Authorization: `Bearer ${tokenB}` },
  })
  check('7d. B không đọc được hàng chờ liên kết', [401, 403].includes(otherRequest.status),
    `http=${otherRequest.status}`)

  if (stopHere('idor')) return finish(browser)

  /* ============ 5. Workflow terminal error ============ */

  const override = `${REPO}/tests/e2e/bad-llm.override.yml`
  console.log('  → đặt cấu hình LLM sai trên stack')
  compose(['up', '-d', 'backend'], { override })
  const wentRed = await waitReady(503, 30)
  check('5a. /ready đỏ khi cấu hình LLM sai', wentRed, 'HTTP 503')

  const wfErr = await sendGoal(pageA, 'Tôi muốn đặt lịch tham quan căn hộ.')
  const errLabel = await waitForCardTerminal(pageA, wfErr, 120000)
  check('5b. UI hiện lỗi terminal, không quay mãi', TERMINAL_LABELS.has(errLabel), `nhãn="${errLabel}"`)

  // Polling phải DỪNG HẲN, không phải dừng ngay lập tức.
  //
  // Sau khi workflow kết thúc, thẻ còn poll thêm vài nhịp có TRẦN để chờ câu
  // trả lời của P-118 (backend sinh nó ở tác vụ nền, xem `useWorkflowPolling`).
  // Vì vậy phép đo đúng là "im lặng ở cuối cửa sổ", chứ không phải "im lặng
  // ngay lập tức" — cách đo sau sẽ đỏ với một hành vi hoàn toàn có chủ ý.
  // Chờ quá TRẦN chờ câu trả lời của hook (30s) rồi mới đo: trong khoảng đó
  // thẻ còn poll để đợi câu trả lời, và đó là hành vi có chủ ý.
  await pageA.waitForTimeout(36000)
  let polls = 0
  const countPolls = (r) => { if (r.url().includes(`/workflows/demo/${wfErr}`)) polls++ }
  pageA.on('request', countPolls)
  await pageA.waitForTimeout(12000)
  pageA.off('request', countPolls)
  check('5c. UI dừng hẳn polling sau lỗi terminal', polls === 0,
    `request trong 12s cuối=${polls}`)

  await pageA.goto(`${APP}/workflow/${wfErr}`)
  await pageA.waitForTimeout(4000)
  const errText = await pageA.locator('body').innerText()
  const dbErr = sql(`SELECT status || '|' || coalesce(error_code,'-') FROM workflows WHERE workflow_id = '${wfErr}'::uuid`)[0]
  check('5d. Reload vẫn thấy lỗi, đọc từ PostgreSQL',
    TERMINAL_LABELS.has(await statusLabel(pageA)) && dbErr.startsWith('FAILED|LLM_CONFIGURATION_ERROR'),
    `DB=${dbErr}`)

  const leaked = ['LLMConfigurationError', 'OPENROUTER_API_KEY', 'postgresql://', 'SELECT ',
    'Traceback', 'EXECUTION_ERROR', 'sk-'].filter((t) => errText.includes(t))
  check('5e. Không lộ exception, SQL, DSN, key hay enum thô', leaked.length === 0,
    leaked.length ? `lộ ${leaked.join(',')}` : 'sạch')

  check('5f. retryable=false dẫn tới "liên hệ hỗ trợ", không mời thử vô hạn',
    /liên hệ bộ phận hỗ trợ/i.test(errText) && !/thử lại sau/i.test(errText),
    errText.replace(/\s+/g, ' ').match(/Hệ thống[^.]*\./)?.[0]?.slice(0, 70) ?? '')

  console.log('  → khôi phục cấu hình')
  compose(['up', '-d', '--force-recreate', 'backend'])
  await waitReady(200)

  check('9. Không có lỗi JavaScript chưa bắt', jsErrors.length === 0,
    jsErrors.slice(0, 2).join(' | ') || 'sạch')

  /* ============ Bằng chứng ============ */

  console.log('\n--- PostgreSQL (p118_db, đã mask) ---')
  console.log('  user A          :', mask(uidA))
  console.log('  liên kết        :', sql(`SELECT verification_status FROM user_resident_links WHERE user_id = '${uidA}'`)[0])
  console.log('  hồ sơ xác thực  :', sql(
    `SELECT status FROM verification_records WHERE applicant_user_id = '${uidA}'`)[0])
  console.log('  workflow của A  :', sql(`SELECT count(*) FROM workflows WHERE owner_user_id = '${uidA}'`)[0])
  console.log('  workflow bị kẹt :', sql(
    "SELECT count(*) FROM workflows WHERE status IN ('PENDING','RUNNING') AND archived_at IS NULL "
    + "AND updated_at < NOW() - INTERVAL '5 minutes'")[0])
  console.log('  register_resident:', sql(
    `SELECT count(*) FROM workflow_tasks t JOIN workflows w ON w.workflow_id = t.workflow_id
     WHERE w.owner_user_id = '${uidA}' AND t.tool = 'register_resident'`)[0])

  return finish(browser)
}

async function finish(browser) {
  await browser.close()
  const ok = RESULTS.filter((r) => r.ok).length
  console.log(`\n=== ${ok}/${RESULTS.length} PASS ===`)
  RESULTS.filter((r) => !r.ok).forEach((r) => console.log(`  FAIL: ${r.name} — ${r.detail}`))
  process.exit(ok === RESULTS.length ? 0 : 1)
}

main().catch(async (e) => {
  console.error(`\nDỪNG: ${e.constructor.name}: ${e.message}`)
  // Cấu hình sai KHÔNG được để lại cho lần chạy sau.
  try {
    compose(['up', '-d', '--force-recreate', 'backend'])
  } catch { /* stack có thể đã tắt */ }
  process.exit(2)
})
