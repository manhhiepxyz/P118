import { Link } from 'react-router-dom'
import { useState } from 'react'
import { ChevronRight, ExternalLink, Home, Mail, Minus, Phone, Plus, Rocket, Search, Send, ShieldCheck } from 'lucide-react'

import { WorkspaceShell } from '../components/workspace/WorkspaceShell'
import { useAuth } from '../lib/auth'

/**
 * Hỗ trợ — câu hỏi thường gặp, quyền riêng tư, và cách liên hệ.
 *
 * Ba nguyên tắc, cả ba đến từ những lỗi đã gặp trong chính sản phẩm này:
 *
 *  1. **Không hứa thứ không có.** Số điện thoại và email đọc từ cấu hình
 *     (`VITE_SUPPORT_*`). Chưa đặt thì khối liên hệ KHÔNG hiện, thay vì bày ra
 *     một hotline bịa. Một số điện thoại giả còn tệ hơn không có số nào: người
 *     dùng sẽ gọi, không ai bắt máy, và họ mất niềm tin vào mọi thứ khác trên
 *     trang.
 *
 *  2. **Chỉ đường bằng tên CÓ THẬT.** Mọi hướng dẫn nêu đúng nhãn đang hiện
 *     trên màn hình. Sản phẩm này từng bảo người dùng mở mục "Liên kết căn hộ"
 *     — một mục không tồn tại — và họ đi tìm mãi không ra.
 *
 *  3. **Quyền riêng tư nói đúng thứ code làm**, không phải một bản mẫu pháp lý.
 *     Từng dòng dưới đây đối chiếu được với mã nguồn.
 */

/** Rỗng thì khối liên hệ tự ẩn. Đặt trong `.env` của frontend khi có thật. */
const SUPPORT_EMAIL = import.meta.env.VITE_SUPPORT_EMAIL ?? ''
const SUPPORT_PHONE = import.meta.env.VITE_SUPPORT_PHONE ?? ''
const SUPPORT_HOURS = import.meta.env.VITE_SUPPORT_HOURS ?? ''

/**
 * Câu hỏi thường gặp.
 *
 * Nội dung khớp với `_HOWTO_STEPS` phía backend — cùng một việc thì trang này
 * và P-118 phải nói giống nhau. Lệch một chỗ là người dùng nhận hai hướng dẫn
 * khác nhau cho cùng một câu hỏi.
 */
const FAQ: { q: string; a: string }[] = [
  {
    q: 'Làm sao để xác minh căn hộ?',
    a:
      'Mở mục “Hồ sơ” ở thanh bên, bấm “Xác minh căn hộ”, rồi bấm “Xác thực với đơn vị”. ' +
      'Ở cổng của đơn vị xác thực, nhập mã căn hộ và khu đô thị, đính kèm ảnh giấy tờ nhà rồi gửi. ' +
      'Duyệt xong là các dịch vụ cư dân mở ra ngay.',
  },
  {
    q: 'Vì sao P-118 không tự xác minh căn hộ cho tôi?',
    a:
      'Việc đối chiếu giấy tờ chủ sở hữu do một đơn vị độc lập thực hiện, không phải P-118. ' +
      'Trợ lý chỉ dẫn bạn tới đúng chỗ và hỗ trợ tiếp sau khi hồ sơ được duyệt.',
  },
  {
    q: 'Dịch vụ nào dùng được khi chưa xác minh căn hộ?',
    a:
      'Đặt lịch tham quan dự án và Đăng ký quan tâm / nhận tư vấn. ' +
      'Đăng ký xe, chỗ đỗ, báo bảo trì và đặt lịch chuyển nhà cần căn hộ đã xác minh.',
  },
  {
    q: 'Đặt lịch tham quan bao lâu thì được xác nhận?',
    a:
      'Yêu cầu được gửi tới đơn vị tổ chức tham quan và chờ họ xác nhận. ' +
      'P-118 báo lại ngay trong hội thoại khi có kết quả — bạn không cần chờ trên màn hình.',
  },
  {
    q: 'Tôi nói “ngày mai” hay “thứ Bảy này” được không?',
    a:
      'Được. P-118 hiểu ngày tương đối và tự quy ra ngày cụ thể. ' +
      'Riêng “tuần sau” hay “cuối tuần” thì vẫn hỏi lại, vì hai cách nói đó chưa xác định được một ngày.',
  },
  {
    q: 'Tôi đang điền dở một yêu cầu, hỏi việc khác được không?',
    a:
      'Được. Cứ hỏi ngay trong ô chat — P-118 trả lời rồi giữ nguyên câu hỏi đang chờ, ' +
      'bạn quay lại trả lời tiếp lúc nào cũng được.',
  },
]

/**
 * Quyền riêng tư — mô tả đúng thứ hệ thống làm.
 *
 * Mỗi dòng đối chiếu được với mã nguồn; không có dòng nào là văn mẫu.
 */
const PRIVACY: { title: string; body: string }[] = [
  {
    title: 'Giấy tờ tuỳ thân',
    body:
      'Hệ thống chỉ lưu 4 số cuối CCCD để nhận diện hồ sơ. Số đầy đủ không được nhập và không được lưu ở đâu cả.',
  },
  {
    title: 'Ảnh giấy tờ nhà',
    body:
      'Ảnh bạn tải lên đi thẳng tới đơn vị xác thực để đối chiếu chủ sở hữu. ' +
      'P-118 nhận lại đúng một kết quả “khớp” hoặc “không khớp” — tên chủ hộ trong hồ sơ gốc không bao giờ được trả về trợ lý.',
  },
  {
    title: 'Nội dung bạn trò chuyện',
    body:
      'Câu bạn gõ được gửi tới nhà cung cấp mô hình ngôn ngữ để hiểu yêu cầu và soạn câu trả lời. ' +
      'Đừng gõ số CCCD đầy đủ, số tài khoản ngân hàng hay mật khẩu vào ô chat.',
  },
  {
    title: 'Phiên đăng nhập',
    body:
      'Phiên được giữ trong bộ nhớ của tab trình duyệt và mất khi bạn đóng tab. ' +
      'Bấm đăng xuất ở góc phải trên để kết thúc ngay.',
  },
  {
    title: 'Dữ liệu yêu cầu',
    body:
      'Lịch sử yêu cầu và kết quả được lưu để bạn xem lại ở mục “Lịch sử”. ' +
      'Xoá một yêu cầu chỉ ẩn nó khỏi danh sách; chứng từ thanh toán được giữ lại theo yêu cầu đối soát.',
  },
]

const TOPICS = [
  { value: 'sai', label: 'P-118 trả lời sai hoặc chưa hiểu' },
  { value: 'loi', label: 'Lỗi khi dùng — bấm không chạy, hiện sai' },
  { value: 'de-xuat', label: 'Đề xuất tính năng' },
  { value: 'khac', label: 'Việc khác' },
] as const

/**
 * Góp ý — soạn sẵn một email, KHÔNG tự gửi đi đâu.
 *
 * Hệ thống chưa có endpoint nhận góp ý. Dựng một form `POST` vào hư không là
 * kiểu hứa tệ nhất: người dùng viết xong, thấy "đã gửi", rồi không ai đọc.
 *
 * Nên nút này mở ứng dụng thư của chính họ với nội dung đã điền sẵn. Thư đi từ
 * hộp thư của họ nên có sẵn đường trả lời, và không có gì phải tin ở giữa.
 * Chưa cấu hình địa chỉ nhận thì nút TẮT kèm lý do — không giả vờ gửi được.
 *
 * Phần "thông tin kèm theo" hiện rõ trước khi bấm. Đính kèm ngầm tên tài khoản
 * và thời điểm vào một email là gửi dữ liệu người dùng đi mà không hỏi họ —
 * ngay dưới mục nói về quyền riêng tư thì càng không thể làm vậy.
 */
function FeedbackForm() {
  const { user } = useAuth()
  const [topic, setTopic] = useState<string>(TOPICS[0].value)
  const [message, setMessage] = useState('')

  const label = TOPICS.find((t) => t.value === topic)?.label ?? 'Góp ý'
  const ready = Boolean(SUPPORT_EMAIL) && message.trim().length >= 10

  function send() {
    const subject = `[P-118] ${label}`
    const body = [
      message.trim(),
      '',
      '---',
      user?.username ? `Tài khoản: ${user.username}` : null,
      `Gửi lúc: ${new Date().toLocaleString('vi-VN')}`,
    ]
      .filter(Boolean)
      .join('\n')
    window.location.href = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
      subject,
    )}&body=${encodeURIComponent(body)}`
  }

  const control =
    'mt-1.5 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] px-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--selection)]'

  return (
    <div className="mt-6 rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-5">
      <h3 className="text-[15.5px] font-semibold text-[var(--text-primary)]">Gửi góp ý</h3>

      <label htmlFor="fb-topic" className="block text-[13px] font-medium text-[var(--text-secondary)]">
        Nội dung về
      </label>
      <select
        id="fb-topic"
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        className={`${control} h-11`}
      >
        {TOPICS.map((t) => (
          <option key={t.value} value={t.value}>
            {t.label}
          </option>
        ))}
      </select>

      <label htmlFor="fb-message" className="mt-4 block text-[13px] font-medium text-[var(--text-secondary)]">
        Mô tả
      </label>
      <textarea
        id="fb-message"
        rows={4}
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Bạn đang làm gì, mong đợi điều gì, và thực tế xảy ra thế nào?"
        className={`${control} resize-y py-2.5 leading-[1.6]`}
      />
      <p className="mt-1.5 text-[12.5px] text-[var(--text-muted)]">
        Càng cụ thể càng dễ sửa. Đừng gõ số CCCD đầy đủ hay mật khẩu vào đây.
      </p>

      {/* Nói TRƯỚC những gì sẽ đi kèm — không đính kèm ngầm. */}
      <p className="mt-3 text-[12.5px] leading-[1.55] text-[var(--text-muted)]">
        Thư sẽ kèm {user?.username ? <>tên tài khoản <b>{user.username}</b> và </> : null}thời điểm gửi.
      </p>

      <button
        type="button"
        onClick={send}
        disabled={!ready}
        className="press mt-4 inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-[var(--r-sm)] px-4 text-[14.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-40"
        style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
      >
        <Send className="h-4 w-4" strokeWidth={2.2} aria-hidden />
        Soạn thư góp ý
      </button>

      {/* Nút tắt thì phải nói VÌ SAO. Một nút mờ không lý do đọc như hỏng. */}
      {!SUPPORT_EMAIL ? (
        <p className="mt-3 text-[13px] leading-[1.6]" style={{ color: 'var(--waiting-user)' }}>
          Chưa cấu hình hộp thư nhận góp ý, nên nút này tạm tắt. Trong lúc chờ, bạn nhắn thẳng cho
          P-118 ở ô chat — nội dung vẫn tới được người phụ trách.
        </p>
      ) : (
        message.trim().length > 0 &&
        message.trim().length < 10 && (
          <p className="mt-3 text-[13px] text-[var(--text-muted)]">Viết thêm vài chữ nữa giúp mình nhé.</p>
        )
      )}
    </div>
  )
}


/**
 * Ba lối vào nhanh, đặt ngay dưới hero.
 *
 * Mỗi thẻ dẫn tới một chỗ CÓ THẬT — hai trang trong sản phẩm và một mục trên
 * chính trang này. Thẻ "Tìm hiểu thêm" mà không đi đâu là thứ đầu tiên người
 * dùng phát hiện ra là giả.
 */
const ENTRIES: {
  to: string
  Icon: typeof Rocket
  title: string
  body: string
  cta: string
}[] = [
  {
    to: '/workspace',
    Icon: Rocket,
    title: 'Bắt đầu dùng',
    body: 'Đặt lịch tham quan dự án hoặc đăng ký nhận tư vấn — hai việc dùng được ngay, không cần xác minh gì.',
    cta: 'Mở không gian làm việc',
  },
  {
    to: '/profile',
    Icon: Home,
    title: 'Xác minh căn hộ',
    body: 'Mở khoá đăng ký xe, chỗ đỗ, báo bảo trì và đặt lịch chuyển nhà. Đơn vị xác thực duyệt hồ sơ.',
    cta: 'Vào Hồ sơ',
  },
  {
    to: '#privacy-heading',
    Icon: ShieldCheck,
    title: 'Dữ liệu của bạn',
    body: 'Những gì được lưu, những gì không, và dữ liệu đi tới đâu khi bạn trò chuyện với P-118.',
    cta: 'Xem chi tiết',
  },
]

export function SupportPage() {
  const hasContact = Boolean(SUPPORT_EMAIL || SUPPORT_PHONE)
  const [query, setQuery] = useState('')

  // Tìm THẬT, lọc ngay trên danh sách. Một ô tìm kiếm không tìm gì cả là lời
  // hứa rẻ nhất và bị phát hiện nhanh nhất.
  //
  // Bỏ dấu hai đầu để "xac minh" tìm được "xác minh" — người dùng gõ nhanh
  // thường không bỏ dấu, và bắt họ gõ đúng dấu mới ra kết quả là bắt sai người.
  const norm = (t: string) =>
    t
      .toLowerCase()
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/đ/g, 'd')
  const needle = norm(query.trim())
  const shown = needle ? FAQ.filter((f) => norm(`${f.q} ${f.a}`).includes(needle)) : FAQ

  return (
    <WorkspaceShell>
      <div className="h-full overflow-y-auto">
        {/* `seq` — các khối vào so le, cùng ngôn ngữ chuyển động với Hành
            trình và Lịch sử. Áp được thẳng lên container vì năm khối con ở đây
            luôn tồn tại; không khối nào render có điều kiện, nên `nth-child`
            không bao giờ xô lệch. */}
        <div className="seq mx-auto w-full max-w-[1100px] px-10 pb-20 pt-14">
          {/* ── Hero ─────────────────────────────────────────────── */}
          <div className="text-center">
            <h1 className="text-[38px] font-semibold leading-[1.12] tracking-[-0.03em] text-[var(--text-primary)]">
              Cần giúp gì không?
            </h1>
            <p className="mx-auto mt-3.5 max-w-[52ch] text-[16px] leading-[1.6] text-[var(--text-secondary)]">
              Tìm câu trả lời bên dưới, hoặc hỏi thẳng P-118 trong ô chat — trợ lý trả lời được hầu
              hết các câu ở đây.
            </p>

            <div className="mx-auto mt-7 flex max-w-[520px] items-center gap-2">
              <div className="relative flex-1">
                <Search
                  className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-muted)]"
                  strokeWidth={2}
                  aria-hidden
                />
                <label htmlFor="faq-search" className="sr-only">
                  Tìm trong câu hỏi thường gặp
                </label>
                <input
                  id="faq-search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Tìm câu hỏi…"
                  className="h-11 w-full rounded-[var(--r-sm)] border border-[var(--border-subtle)] bg-[var(--surface-overlay)] pl-10 pr-3.5 text-[15px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--selection)]"
                />
              </div>
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery('')}
                  className="press h-11 shrink-0 cursor-pointer rounded-[var(--r-sm)] border border-[var(--border-strong)] px-3.5 text-[14px] font-medium text-[var(--text-secondary)]"
                >
                  Xoá
                </button>
              )}
            </div>
          </div>

          {/* ── Ba lối vào ───────────────────────────────────────── */}
          <div className="mt-12 grid gap-5 md:grid-cols-3">
            {ENTRIES.map(({ to, Icon, title, body, cta }) => (
              <Link
                key={title}
                to={to}
                className="press group flex flex-col rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-6 transition-colors hover:border-[var(--border-strong)]"
              >
                <span
                  className="flex h-11 w-11 items-center justify-center rounded-[var(--r-md)]"
                  style={{ backgroundColor: 'color-mix(in srgb, var(--agent) 12%, transparent)' }}
                  aria-hidden
                >
                  <Icon className="h-5 w-5" strokeWidth={2} style={{ color: 'var(--agent)' }} />
                </span>
                <h2 className="mt-5 text-[17px] font-semibold text-[var(--text-primary)]">{title}</h2>
                <p className="mt-2 flex-1 text-[14.5px] leading-[1.6] text-[var(--text-secondary)]">{body}</p>
                <span className="mt-5 inline-flex items-center gap-1.5 text-[14px] font-medium text-[var(--agent)]">
                  {cta}
                  <ChevronRight
                    className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                    strokeWidth={2.4}
                    aria-hidden
                  />
                </span>
              </Link>
            ))}
          </div>

          {/* ── FAQ ──────────────────────────────────────────────── */}
          <section
            className="mt-6 rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-8"
            aria-labelledby="faq-heading"
          >
            <div className="grid gap-10 lg:grid-cols-[280px_1fr]">
              <div>
                <h2 id="faq-heading" className="text-[24px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
                  Câu hỏi thường gặp
                </h2>
                <p className="mt-3 text-[14.5px] leading-[1.65] text-[var(--text-secondary)]">
                  Những thứ người dùng hay hỏi nhất. Không thấy câu của bạn ở đây?
                </p>

                {hasContact ? (
                  <div className="mt-4 space-y-2">
                    {SUPPORT_EMAIL && (
                      <p className="flex items-center gap-2 text-[14.5px] text-[var(--text-primary)]">
                        <Mail className="h-4 w-4 shrink-0 text-[var(--text-muted)]" strokeWidth={2} aria-hidden />
                        <a href={`mailto:${SUPPORT_EMAIL}`} className="break-all hover:underline">
                          {SUPPORT_EMAIL}
                        </a>
                      </p>
                    )}
                    {SUPPORT_PHONE && (
                      <p className="flex items-center gap-2 text-[14.5px] text-[var(--text-primary)]">
                        <Phone className="h-4 w-4 shrink-0 text-[var(--text-muted)]" strokeWidth={2} aria-hidden />
                        <a href={`tel:${SUPPORT_PHONE.replace(/\s/g, '')}`} className="hover:underline">
                          {SUPPORT_PHONE}
                        </a>
                        {SUPPORT_HOURS && (
                          <span className="text-[13px] text-[var(--text-muted)]">· {SUPPORT_HOURS}</span>
                        )}
                      </p>
                    )}
                  </div>
                ) : (
                  /* Không bịa một hotline. Nói thật là chưa có, và chỉ đường tới
                     kênh CÓ THẬT — ban quản lý toà nhà. */
                  <p className="mt-4 text-[14px] leading-[1.6] text-[var(--text-muted)]">
                    Chưa có kênh hỗ trợ trực tiếp. Việc liên quan tới căn hộ, hồ sơ cư dân hay phí
                    dịch vụ, bạn liên hệ ban quản lý toà nhà.
                  </p>
                )}
              </div>

              {/* `<details>` chứ không phải accordion tự viết: mở/đóng được bằng
                  bàn phím, Ctrl+F thấy cả nội dung đang đóng, không cần JS. */}
              <div className="min-w-0">
                {shown.length === 0 ? (
                  <p className="text-[15px] text-[var(--text-secondary)]">
                    Không có câu nào khớp “{query}”. Bạn thử hỏi thẳng P-118 trong ô chat nhé.
                  </p>
                ) : (
                  <div className="divide-y divide-[var(--border-subtle)]">
                    {shown.map((item) => (
                      <details key={item.q} className="group py-4 first:pt-0">
                        <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[15.5px] font-medium text-[var(--text-primary)]">
                          {item.q}
                          <span
                            aria-hidden
                            className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-[var(--border-strong)] text-[var(--text-secondary)] transition-colors group-open:border-transparent"
                            style={{}}
                          >
                            <Plus className="h-3.5 w-3.5 group-open:hidden" strokeWidth={2.4} />
                            <Minus className="hidden h-3.5 w-3.5 group-open:block" strokeWidth={2.4} />
                          </span>
                        </summary>
                        <p className="mt-2.5 pr-10 text-[14.5px] leading-[1.7] text-[var(--text-secondary)]">
                          {item.a}
                        </p>
                      </details>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* ── Dữ liệu của bạn ──────────────────────────────────── */}
          <section
            className="mt-6 rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-8"
            aria-labelledby="privacy-heading"
          >
            <div className="grid gap-10 lg:grid-cols-[280px_1fr]">
              <div>
                <h2
                  id="privacy-heading"
                  className="scroll-mt-8 text-[24px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]"
                >
                  Dữ liệu của bạn
                </h2>
                <p className="mt-3 text-[14.5px] leading-[1.65] text-[var(--text-secondary)]">
                  Mô tả đúng thứ hệ thống làm, không phải một bản mẫu pháp lý.
                </p>
              </div>
              <dl className="grid min-w-0 gap-x-8 gap-y-6 sm:grid-cols-2">
                {PRIVACY.map((item) => (
                  <div key={item.title}>
                    <dt className="text-[15px] font-medium text-[var(--text-primary)]">{item.title}</dt>
                    <dd className="mt-1.5 text-[14px] leading-[1.65] text-[var(--text-secondary)]">{item.body}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </section>

          {/* ── Góp ý ────────────────────────────────────────────── */}
          <section
            className="mt-6 rounded-[var(--r-lg)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-8"
            aria-labelledby="feedback-heading"
          >
            <div className="grid gap-10 lg:grid-cols-[280px_1fr]">
              <div>
                <h2
                  id="feedback-heading"
                  className="text-[24px] font-semibold tracking-[-0.02em] text-[var(--text-primary)]"
                >
                  Gửi góp ý
                </h2>
                <p className="mt-3 text-[14.5px] leading-[1.65] text-[var(--text-secondary)]">
                  P-118 trả lời sai, bấm không chạy, hay bạn muốn thêm gì — cứ nói thẳng.
                </p>
                <Link
                  to="/workspace"
                  className="press mt-4 inline-flex min-h-11 items-center gap-2 rounded-[var(--r-sm)] border px-4 text-[14.5px] font-medium transition-colors"
                  style={{ borderColor: 'var(--border-strong)', color: 'var(--text-secondary)' }}
                >
                  Hỏi P-118 ngay
                  <ExternalLink className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                </Link>
              </div>
              <div className="min-w-0">
                <FeedbackForm />
              </div>
            </div>
          </section>
        </div>
      </div>
    </WorkspaceShell>
  )
}
