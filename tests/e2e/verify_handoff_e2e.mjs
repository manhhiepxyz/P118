/**
 * Luồng xác minh căn hộ — hai lỗi đã tái hiện được, kiểm bằng trình duyệt thật.
 *
 * 1. `GET /verification-records/my` trả `ORDER BY created_at` TĂNG DẦN, còn cả
 *    `ApartmentLinkPage` lẫn `VerifyApartmentPage` đều lấy `records[0]` rồi đặt
 *    tên là `latest`. Người bị từ chối rồi gửi lại thấy banner đỏ "Chưa được
 *    duyệt" kèm lý do cũ, trong khi đơn thật của họ đang PENDING bình thường.
 *
 * 2. `/apartment-link` → `/verify` là chuyển route trong cùng ứng dụng nên xảy
 *    ra tức thì. Không có gì báo rằng người dùng vừa vượt một ranh giới tin cậy
 *    và sắp tải ảnh sổ hồng lên hệ thống khác.
 *
 * Chạy:
 *   P118_APP=http://127.0.0.1:5299 P118_ADMIN_PASSWORD=… \
 *     node tests/e2e/verify_handoff_e2e.mjs
 */

import { chromium } from 'playwright'

const APP = process.env.P118_APP ?? 'http://127.0.0.1:5273'
const API = process.env.P118_API ?? 'http://127.0.0.1:8000'
// Người DUYỆT là provider, không phải admin. Hai vai riêng: admin quản trị
// P-118, provider là đơn vị xác thực bên thứ 3 (`scripts/create_provider.py`).
const PROVIDER_USER = process.env.P118_PROVIDER_USERNAME ?? 'provider'
const PROVIDER_PASS = process.env.P118_PROVIDER_PASSWORD
const ADMIN_USER = process.env.P118_ADMIN_USERNAME ?? 'admin'
const ADMIN_PASS = process.env.P118_ADMIN_PASSWORD
const PASSWORD = 'Probe12345!'

// PNG 1x1 hợp lệ — đủ để qua whitelist content-type của backend.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}

async function api(path, { method = 'GET', token, json, form } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (json) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API}/api/v1${path}`, {
    method,
    headers,
    body: json ? JSON.stringify(json) : form,
  })
  return { status: res.status, body: await res.json().catch(() => null) }
}

async function login(username, password) {
  const { body } = await api('/auth/login', { method: 'POST', json: { username, password } })
  return body?.access_token
}

async function submitApartment(token, fullName) {
  const form = new FormData()
  form.set('record_type', 'apartment')
  form.set(
    'claimed_data',
    JSON.stringify({
      apartment_code: 'A1201',
      residential_area: 'Vinhomes Ocean Park',
      full_name: fullName,
    }),
  )
  form.set('files', new Blob([PNG], { type: 'image/png' }), 'proof.png')
  const { status, body } = await api('/verification-records', { method: 'POST', token, form })
  return { status, recordId: body?.item?.record_id, detail: body?.detail }
}

async function submitApartmentAs(token, apartmentCode, fullName) {
  const form = new FormData()
  form.set('record_type', 'apartment')
  form.set(
    'claimed_data',
    JSON.stringify({ apartment_code: apartmentCode, residential_area: 'Vinhomes Ocean Park', full_name: fullName }),
  )
  form.set('files', new Blob([PNG], { type: 'image/png' }), 'proof.png')
  const { status, body } = await api('/verification-records', { method: 'POST', token, form })
  return { status, recordId: body?.item?.record_id, detail: body?.detail }
}

// Token của NGƯỜI KHÁC để dọn hồ sơ provider tự nộp — chính provider không dọn
// được, đó là điểm của cả bài kiểm tra này. Admin đóng vai đường phá kính.
let _adminToken = null
function reviewerToken2() {
  return _adminToken
}

async function main() {
  if (!PROVIDER_PASS) throw new Error('Cần P118_PROVIDER_PASSWORD để duyệt/từ chối hồ sơ.')

  //
  // Hỏng ở đây phải HÉT LÊN ngay. Bản trước dùng `pending?.items ?? []`: sai
  // mật khẩu provider → token `undefined` → list trả 401 → `items` không tồn
  // tại → vòng lặp chạy 0 lần → không dọn gì → và lỗi duy nhất người chạy nhìn
  // thấy là "Không gửi được đơn 1: 409 Căn hộ này đang có một hồ sơ chờ
  // duyệt". Triệu chứng cách nguyên nhân ba bước và không nhắc gì tới xác
  // thực. Đúng chuyện đã xảy ra.
  const reviewerToken = await login(PROVIDER_USER, PROVIDER_PASS)
  if (!reviewerToken) {
    throw new Error(
      `Đăng nhập provider thất bại (user=${PROVIDER_USER}). Kiểm tra P118_PROVIDER_PASSWORD, ` +
        'hoặc tạo tài khoản: P118_PROVIDER_USERNAME=provider P118_PROVIDER_PASSWORD=… ' +
        'python scripts/create_provider.py',
    )
  }

  const listed = await api('/verification-records?record_type=apartment&status=PENDING', {
    token: reviewerToken,
  })
  if (listed.status !== 200 || !Array.isArray(listed.body?.items)) {
    throw new Error(
      `Không đọc được danh sách hồ sơ chờ duyệt: HTTP ${listed.status} ` +
        `${JSON.stringify(listed.body)?.slice(0, 200)}`,
    )
  }

  // Mã căn hộ dùng chung một hàng trong registry, mà ràng buộc PENDING là trên
  // CĂN HỘ chứ không phải trên người nộp — nên phải dọn đơn treo của lần chạy
  // trước, nếu không lần này ăn 409 ngay bước đầu.
  for (const record of listed.body.items) {
    if (record.claimed_data?.apartment_code !== 'A1201') continue
    const cleaned = await api(`/verification-records/${record.record_id}/decide`, {
      method: 'POST',
      token: reviewerToken,
      json: { decision: 'reject', reject_reason: 'Dọn dữ liệu treo của lần chạy E2E trước' },
    })
    if (cleaned.status !== 200) {
      throw new Error(
        `Không dọn được hồ sơ treo ${record.record_id}: HTTP ${cleaned.status} ` +
          `${JSON.stringify(cleaned.body)?.slice(0, 200)}`,
      )
    }
  }

  const username = `e2e_verify_${Date.now().toString(36)}`
  await api('/auth/register', { method: 'POST', json: { username, password: PASSWORD } })
  const token = await login(username, PASSWORD)

  // Đơn 1 — sai tên chủ hộ, sẽ bị từ chối.
  const first = await submitApartment(token, 'Sai Ten Chu Ho')
  if (!first.recordId) throw new Error(`Không gửi được đơn 1: ${first.status} ${first.detail}`)
  await api(`/verification-records/${first.recordId}/decide`, {
    method: 'POST',
    token: reviewerToken,
    json: { decision: 'reject', reject_reason: 'Tên không khớp hồ sơ căn hộ' },
  })

  // Đơn 2 — gửi lại. Đây mới là trạng thái thật của người dùng.
  const second = await submitApartment(token, 'Nguyen Van A')
  if (!second.recordId) throw new Error(`Không gửi lại được: ${second.status} ${second.detail}`)

  const { body: mine } = await api('/verification-records/my', { token })
  check(
    'backend vẫn trả thứ tự tăng dần (tiền đề của lỗi)',
    mine.items[0].status === 'REJECTED' && mine.items[1].status === 'PENDING',
    mine.items.map((r) => r.status).join(' → '),
  )

  const browser = await chromium.launch()
  const context = await browser.newContext()
  const page = await context.newPage()

  try {
    await page.goto(`${APP}/login`)
    await page.getByLabel(/tên đăng nhập/i).fill(username)
    await page.getByLabel(/mật khẩu/i).first().fill(PASSWORD)
    await page.getByRole('button', { name: /đăng nhập/i }).click()
    await page.waitForURL((url) => !url.pathname.startsWith('/login'), { timeout: 15000 })

    // --- Trang cửa vào: phải nói PENDING, không phải REJECTED cũ ---
    await page.goto(`${APP}/apartment-link`)
    await page.getByText(/đang chờ|chưa được duyệt|đã được duyệt/i).first().waitFor({ timeout: 15000 })
    const entryText = await page.locator('main, body').first().innerText()
    check(
      'cửa vào hiện đơn MỚI NHẤT (đang chờ), không phải đơn cũ đã bị từ chối',
      /đang chờ đơn vị xác thực duyệt/i.test(entryText) && !/chưa được duyệt/i.test(entryText),
      entryText.split('\n').find((l) => /chờ|duyệt/i.test(l)) ?? '',
    )
    check(
      'không còn hiện lý do từ chối của đơn cũ',
      !/tên không khớp hồ sơ căn hộ/i.test(entryText),
    )

    // --- Chuyển giao sang cổng bên thứ 3 ---
    const handoffSeen = page
      .getByText(/đang chuyển sang cổng của đơn vị xác thực/i)
      .waitFor({ timeout: 5000 })
      .then(() => true)
      .catch(() => false)
    await page.getByRole('link', { name: /xem hồ sơ đã gửi|xác thực với đơn vị/i }).click()
    check('có màn chuyển giao khi rời P-118 sang cổng xác thực', await handoffSeen)

    await page.waitForURL(/\/verify/, { timeout: 15000 })
    await page.getByText(/đang chờ đơn vị xác thực duyệt/i).waitFor({ timeout: 15000 })
    const portalText = await page.locator('body').innerText()
    check(
      'cổng xác thực cũng hiện đơn mới nhất',
      /đang chờ đơn vị xác thực duyệt/i.test(portalText) && !/chưa được duyệt/i.test(portalText),
    )
    check(
      'đang chờ duyệt thì không mở form nộp thêm (nộp nữa sẽ ăn 409)',
      !(await page.getByRole('button', { name: /gửi hồ sơ xác minh/i }).isVisible().catch(() => false)),
    )
    check('người nộp luôn có lối quay lại P-118', await page.getByRole('link', { name: /quay lại p-118/i }).isVisible())

    // --- Trang Hồ sơ: mục Liên kết phải nói ĐANG CHỜ DUYỆT ---
    //
    // `resident_verification_status` suy ra từ `user_resident_links`, còn luồng
    // nộp thật ghi vào `verification_records` và chỉ chạm bảng kia KHI ĐƯỢC
    // DUYỆT. Nên trạng thái chờ không bao giờ tới được trang Hồ sơ: đã đo
    // `/auth/me` trả `NOT_LINKED` ngay sau khi nộp xong.
    await page.goto(`${APP}/profile`)
    await page.getByText(/bất động sản đã liên kết/i).waitFor({ timeout: 15000 })
    // Chờ trạng thái chốt, đừng đọc lúc còn "Đang tải…".
    await page.getByText(/đang chờ duyệt|chưa liên kết|đã xác minh|chưa được duyệt/i)
      .first()
      .waitFor({ timeout: 15000 })
    await page.waitForTimeout(500)
    const profileText = await page.locator('body').innerText()
    check(
      'mục Liên kết trên trang Hồ sơ hiện "Đang chờ duyệt"',
      /đang chờ duyệt/i.test(profileText) && !/chưa liên kết bất động sản nào/i.test(profileText),
      profileText.split('\n').find((l) => /chờ duyệt|chưa liên kết/i.test(l)) ?? '',
    )
    check(
      'hiện luôn căn hộ đã khai, không phải ô rỗng',
      /A1201/.test(profileText),
    )
    check(
      'không còn dòng "Bạn có N hồ sơ đã nộp" ở cuối trang',
      !/hồ sơ đã nộp/i.test(profileText),
    )
  } finally {
    await browser.close()
  }

  // --- Thông báo trùng đơn phải nói đúng ràng buộc ---
  const other = `e2e_other_${Date.now().toString(36)}`
  await api('/auth/register', { method: 'POST', json: { username: other, password: PASSWORD } })
  const otherToken = await login(other, PASSWORD)
  const clash = await submitApartment(otherToken, 'Nguyen Van A')
  check(
    'tài khoản mới toanh không bị bảo là "bạn đã có một đơn"',
    clash.status === 409 && !/bạn đã có/i.test(clash.detail ?? ''),
    clash.detail ?? '',
  )

  // --- Ranh giới hai vai: provider duyệt, admin quản trị ---
  const roleBrowser = await chromium.launch()
  try {
    // Provider: đăng nhập cùng trang `/login`, `HomeRedirect` đẩy thẳng sang
    // cổng duyệt. Không cần mục điều hướng nào — đó LÀ trang chủ của họ.
    const providerPage = await roleBrowser.newPage()
    await providerPage.goto(`${APP}/login`)
    await providerPage.getByLabel(/tên đăng nhập/i).fill(PROVIDER_USER)
    await providerPage.getByLabel(/mật khẩu/i).first().fill(PROVIDER_PASS)
    await providerPage.getByRole('button', { name: /đăng nhập/i }).click()
    const landedOnPortal = await providerPage
      .waitForURL(/\/review/, { timeout: 15000 })
      .then(() => true)
      .catch(() => false)
    check('provider đăng nhập là vào thẳng cổng duyệt', landedOnPortal)
    check(
      'cổng duyệt mang thương hiệu đơn vị xác thực, không phải P-118',
      await providerPage
        .getByText(/cổng xác thực chủ sở hữu/i)
        .first()
        .waitFor({ state: 'visible', timeout: 15000 })
        .then(() => true)
        .catch(() => false),
    )

    // Admin: quản trị P-118, KHÔNG phải người duyệt. Thanh bên không được mời
    // họ sang cổng bên thứ 3 — `require_roles("provider","admin")` phía backend
    // vẫn cho qua, nhưng đó là đường phá kính, không phải điều hướng.
    if (ADMIN_PASS) {
      _adminToken = await login(ADMIN_USER, ADMIN_PASS)
      const adminPage = await roleBrowser.newPage()
      await adminPage.goto(`${APP}/login`)
      await adminPage.getByLabel(/tên đăng nhập/i).fill(ADMIN_USER)
      await adminPage.getByLabel(/mật khẩu/i).first().fill(ADMIN_PASS)
      await adminPage.getByRole('button', { name: /đăng nhập/i }).click()
      await adminPage.waitForURL(/\/admin/, { timeout: 15000 })
      await adminPage.getByRole('link', { name: /quản trị/i }).waitFor({ timeout: 15000 })
      check('admin có mục "Quản trị"', await adminPage.getByRole('link', { name: /quản trị/i }).isVisible())
      check(
        'admin KHÔNG được mời sang cổng duyệt của bên thứ 3',
        (await adminPage.getByRole('link', { name: /duyệt hồ sơ/i }).count()) === 0,
      )
      // Admin VẪN phải vào được hồ sơ của chính mình. Cổng vai chỉ nhắm vào
      // danh tính bên thứ 3, không phải "ai không phải khách hàng thì cấm hết".
      await adminPage.goto(`${APP}/profile`)
      await adminPage.waitForTimeout(1500)
      check('admin vẫn sửa được hồ sơ của chính mình', new URL(adminPage.url()).pathname === '/profile')
    }

    // Provider KHÔNG được lang thang trong bề mặt khách hàng.
    //
    // Trước đây `ProtectedRoute` chỉ hỏi "có phải người đăng nhập không", không
    // hỏi vai — nên provider vào được cả sáu màn khách hàng. Nguy nhất là
    // `/verify`: người duyệt tự nộp được hồ sơ mà chính họ có quyền duyệt.
    for (const path of ['/workspace', '/profile', '/verify', '/apartment-link', '/workflows']) {
      await providerPage.goto(`${APP}${path}`)
      await providerPage.waitForTimeout(1200)
      check(
        `provider bị chặn khỏi ${path}`,
        new URL(providerPage.url()).pathname === '/review',
        `đứng ở ${new URL(providerPage.url()).pathname}`,
      )
    }

    // Chốt backend — thứ thật sự ngăn leo thang quyền. Cổng frontend chỉ ngăn
    // đi lạc; ai gọi thẳng API vẫn phải bị chặn.
    const provToken = await login(PROVIDER_USER, PROVIDER_PASS)
    const own = await submitApartmentAs(provToken, 'SELF-E2E', 'Toi Tu Khai')
    if (own.recordId) {
      const selfDecide = await api(`/verification-records/${own.recordId}/decide`, {
        method: 'POST',
        token: provToken,
        json: { decision: 'approve' },
      })
      check(
        'provider không tự duyệt được hồ sơ của chính mình (API)',
        selfDecide.status === 403,
        `HTTP ${selfDecide.status}`,
      )
      // Dọn: hồ sơ này do bài kiểm tra tạo ra.
      await api(`/verification-records/${own.recordId}/decide`, {
        method: 'POST',
        token: reviewerToken2(),
        json: { decision: 'reject', reject_reason: 'Dọn hồ sơ do E2E tạo' },
      })
    } else {
      check('provider không tự duyệt được hồ sơ của chính mình (API)', false, `nộp hỏng: ${own.detail}`)
    }
  } finally {
    await roleBrowser.close()
  }

  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} PASS`)
  process.exit(failed.length === 0 ? 0 : 1)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
