import { CalendarPlus, Check, Clock, MessageCircle, Navigation, Phone } from 'lucide-react'

import type { AgentTaskResult } from '../../lib/types'

/**
 * Kết quả của MỘT dịch vụ đã xong, trình bày cho NGƯỜI DÙNG.
 *
 * MỘT dịch vụ, không phải một yêu cầu. Một yêu cầu có thể chứa nhiều dịch vụ,
 * và nhiều dịch vụ trong số đó có mốc thời gian riêng — đúng ca mà
 * `ScheduleConflictAction` (`task_a`/`task_b`) sinh ra để xử lý. `WorkflowPage`
 * dựng MỘT thẻ cho MỖI bước như vậy; thành phần này không biết và không cần
 * biết có bao nhiêu thẻ bên cạnh nó.
 *
 * Thẻ trả lời năm câu hỏi khách thật sự có về dịch vụ ấy: đi đâu · khi nào ·
 * xem gì · gặp ai · chuẩn bị gì. Trạng thái kỹ thuật của agent lùi xuống cuối
 * trang, không nằm ở đây.
 *
 * KHÔNG có nút đổi/huỷ, và đó là chủ ý. Mỗi dịch vụ đều cần một lượt xác nhận
 * của đơn vị, và đơn vị gọi cho khách để làm việc ấy — nên đường ngắn nhất để
 * đổi hay huỷ là chính cuộc gọi đó. Một cặp nút gửi vào hàng đợi rồi chờ duyệt
 * là con đường DÀI hơn cho cùng một kết quả, và với "đổi lịch" thì nó còn
 * không dẫn tới đâu: `support_request._ACTIONS` cố ý không có `AMEND` vì "đồng
 * ý cho đổi" chưa nói đổi sang lúc nào.
 *
 * Dòng nhắc "bên dịch vụ sẽ liên hệ" KHÔNG ở đây mà ở `WorkflowPage`, dưới cả
 * nhóm thẻ: nó nói về mọi đơn vị trong yêu cầu. Đặt trong thẻ thì với ba dịch
 * vụ nó lặp ba lần, và mỗi lần đọc như thể chỉ đơn vị của thẻ ấy sẽ gọi.
 *
 * Nhóm thông tin theo NHÃN do backend đặt, KHÔNG theo tên tool — giữ đúng ranh
 * giới đã định: giao diện không suy diễn nghiệp vụ từ `tool`. Nhãn lạ rơi vào
 * nhóm "Khác" và vẫn hiện, nên thêm nghiệp vụ mới không phải sửa file này.
 *
 * Cùng lý do đó, MỌI chữ nêu tên một dịch vụ cụ thể đều đã bị gỡ. Thẻ này từng
 * viết "Lịch tham quan" và "Trước buổi tham quan" cố định, nên khi nó bắt đầu
 * phục vụ chỗ đỗ xe, bảo trì và xe đưa đón thì cả ba hiện ra dưới tên một dịch
 * vụ khác. Tên dịch vụ lấy từ `task.title` — chuỗi backend đã đặt cho đúng tool
 * ấy. Nếu thấy mình sắp gõ tên một dịch vụ vào file này: đừng.
 */

const CONTACT_LABELS = new Set(['Liên hệ', 'Người đón tiếp', 'Số điện thoại', 'Điện thoại'])
const PLACE_LABELS = new Set(['Khu vực đón tiếp', 'Giờ đón tiếp'])
const PHONE_LABELS = new Set(['Số điện thoại', 'Điện thoại'])

interface Props {
  task: AgentTaskResult
}

/** "2026-10-05 10:00" → nhãn tiếng Việt. Không parse được thì trả nguyên văn. */
function readableWhen(raw: string): { day: string; time: string } | null {
  const match = raw.match(/(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/)
  if (!match) return null
  const [, y, m, d, hm] = match
  const date = new Date(Number(y), Number(m) - 1, Number(d))
  const day = date.toLocaleDateString('vi-VN', {
    weekday: 'long',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
  return { day: day.charAt(0).toUpperCase() + day.slice(1), time: hm ?? '' }
}

/**
 * Tệp .ics dựng ngay ở trình duyệt — không cần backend, và là hành động THẬT.
 *
 * `id` đi vào TÊN TỆP. Một yêu cầu có thể chứa hai buổi hẹn (đúng ca mà
 * `ScheduleConflictAction` sinh ra để xử lý), và khách tải cả hai: tên tệp cố
 * định làm trình duyệt lưu thành `... (1).ics`, hai mốc khác nhau nằm dưới hai
 * cái tên không nói được cái nào là cái nào.
 *
 * Tên tệp cũ là `p118-lich-tham-quan.ics` — chữ cứng của MỘT dịch vụ, đúng lỗi
 * `INT-003` mà cả file này được viết lại để tránh. Nó lọt qua bài kiểm vì dấu
 * gạch nối: `test_the_result_card_never_names_one_service` tìm `"tham quan"`
 * có dấu cách.
 */
function downloadIcs(title: string, raw: string, id: string) {
  const match = raw.match(/(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/)
  if (!match) return
  const [, y, m, d, hh = '09', mm = '00'] = match
  const start = `${y}${m}${d}T${hh}${mm}00`
  const endHour = String((Number(hh) + 1) % 24).padStart(2, '0')
  const end = `${y}${m}${d}T${endHour}${mm}00`
  const ics = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//P-118//VI',
    'BEGIN:VEVENT',
    `DTSTART:${start}`,
    `DTEND:${end}`,
    `SUMMARY:${title}`,
    'END:VEVENT',
    'END:VCALENDAR',
  ].join('\r\n')
  const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `p118-${id}.ics`
  link.click()
  URL.revokeObjectURL(url)
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
        {label}
      </dt>
      <dd className="mt-1.5 break-words text-[16px] font-medium leading-[1.4] text-[var(--text-primary)]">
        {value}
      </dd>
    </div>
  )
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
        {title}
      </h2>
      <div className="mt-4">{children}</div>
    </section>
  )
}

export function ResultSummary({ task }: Props) {
  const details = task.details ?? []
  const byLabel = new Map(details.map((detail) => [detail.label, detail.value]))

  const schedule = details.filter(
    (detail) => !CONTACT_LABELS.has(detail.label) && !PLACE_LABELS.has(detail.label),
  )
  const place = details.filter((detail) => PLACE_LABELS.has(detail.label))
  const contactName = byLabel.get('Liên hệ') ?? byLabel.get('Người đón tiếp')
  const phone = details.find((detail) => PHONE_LABELS.has(detail.label))?.value

  const whenRaw = byLabel.get('Thời gian') ?? ''
  const when = whenRaw ? readableWhen(whenRaw) : null

  /*
   * Không có MỐC THỜI GIAN thì đây không phải một buổi hẹn.
   *
   * Bố cục bên dưới nói bằng ngôn ngữ của một lịch tham quan: "Trước buổi tham
   * quan", "Thêm vào lịch", "Đổi lịch", "Huỷ lịch". Áp nó cho mọi kết quả đã
   * xong nghĩa là một lần ĐĂNG KÝ TƯ VẤN — thứ không có giờ, không có điểm gặp,
   * không có gì để đổi — vẫn hiện đủ những nút ấy. Đo được: yêu cầu INT-003
   * hiện "Lịch tham quan · Đổi lịch · Huỷ lịch · Trước buổi tham quan".
   *
   * Phân biệt bằng DỮ KIỆN có thật (`Thời gian`), không bằng tên tool.
   */
  if (!when) {
    return (
      <Block title="Kết quả">
        {details.length === 0 ? (
          <p className="text-[15px] text-[var(--text-secondary)]">
            Yêu cầu đã hoàn tất. Đơn vị sẽ liên hệ với bạn nếu cần thêm thông tin.
          </p>
        ) : (
          <dl className="grid gap-x-10 gap-y-4 sm:grid-cols-2">
            {details.map((detail) => (
              <div key={detail.label}>
                <dt className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
                  {detail.label}
                </dt>
                <dd className="mt-1 text-[16px] font-medium leading-[1.4] text-[var(--text-primary)]">
                  {detail.value}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </Block>
    )
  }

  return (
    <div className="grid gap-12 lg:grid-cols-[minmax(0,1fr)_300px]">
      {/* ── Cột chính: kết quả, rồi chuẩn bị ───────────────────────── */}
      <div className="space-y-11">
        {/* Tiêu đề là TÊN DỊCH VỤ do backend đặt, không phải chữ cứng.
            Trước đây nó là "Lịch tham quan" cố định, nên một chỗ đỗ xe hay một
            lịch bảo trì cũng hiện ra dưới cái tên ấy. Đúng lỗi `INT-003` mà
            cổng `if (!when)` phía trên tồn tại để chặn — chỉ đổi nạn nhân. */}
        <Block title={task.title}>
          {when && (
            <div className="mb-6">
              <p className="text-[24px] font-semibold leading-[1.25] tracking-[-0.02em] text-[var(--text-primary)]">
                {when.day}
              </p>
              {when.time && (
                <p className="mt-1.5 flex items-center gap-2 font-mono text-[18px] tabular-nums text-[var(--text-secondary)]">
                  <Clock className="h-4 w-4" strokeWidth={2} aria-hidden />
                  {when.time}
                </p>
              )}
            </div>
          )}

          <dl className="grid gap-x-10 gap-y-5 sm:grid-cols-2">
            {schedule
              .filter((detail) => detail.label !== 'Thời gian')
              .map((detail) => (
                <Field key={detail.label} label={detail.label} value={detail.value} />
              ))}
          </dl>

          {/* Tên sự kiện trong `.ics` là tên của CHÍNH buổi hẹn này, không
              phải tiêu đề của cả yêu cầu. Một yêu cầu hai buổi hẹn từng đẩy
              MỘT tiêu đề chung xuống đây, nên hai mốc vào lịch điện thoại dưới
              cùng một cái tên — và đó là tên của một trong hai dịch vụ. */}
          <div className="mt-7 flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={() => whenRaw && downloadIcs(task.title, whenRaw, task.task_id)}
              disabled={!whenRaw}
              className="press inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-[var(--r-sm)] px-4 text-[14px] font-semibold disabled:opacity-40"
              style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
            >
              <CalendarPlus className="h-4 w-4" strokeWidth={2.2} aria-hidden />
              Thêm vào lịch
            </button>
          </div>
        </Block>

        <Block title="Trước buổi hẹn">
          <ul className="space-y-2.5">
            {/* Hai dòng đầu suy từ trạng thái THẬT. */}
            <li className="flex items-start gap-2.5 text-[15px] leading-[1.5] text-[var(--text-primary)]">
              <Check
                className="mt-0.5 h-4 w-4 shrink-0"
                style={{ color: 'var(--success)' }}
                strokeWidth={2.6}
                aria-hidden
              />
              Lịch đã được xác nhận
            </li>
            {contactName && (
              <li className="flex items-start gap-2.5 text-[15px] leading-[1.5] text-[var(--text-primary)]">
                <Check
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: 'var(--success)' }}
                  strokeWidth={2.6}
                  aria-hidden
                />
                Chuyên viên phụ trách đã được phân công
              </li>
            )}
          </ul>

          {/* Gợi ý — KHÔNG phải quy định. Backend không có luật nào như vậy,
              nên chúng được tách khỏi phần tick xanh phía trên và ghi rõ là
              gợi ý, tránh người dùng hiểu thành yêu cầu bắt buộc. */}
          <div className="mt-5 border-t border-[var(--border-subtle)] pt-4">
            <p className="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--text-muted)]">
              Gợi ý
            </p>
            <ul className="mt-2.5 space-y-1.5 text-[14.5px] leading-[1.55] text-[var(--text-secondary)]">
              <li>Có mặt trước khoảng 10 phút.</li>
              <li>Mang theo giấy tờ cá nhân nếu được yêu cầu.</li>
            </ul>
          </div>
        </Block>
      </div>

      {/* ── Cột phụ: nơi gặp và người gặp ──────────────────────────── */}
      <div className="space-y-11">
        <Block title="Địa điểm">
          {place.length > 0 ? (
            <dl className="space-y-4">
              {place.map((detail) => (
                <Field key={detail.label} label={detail.label} value={detail.value} />
              ))}
            </dl>
          ) : (
            /* Không bịa địa chỉ. Backend chưa trả `meeting_location` cho lượt
               chạy này, nên nói thật là chưa có thay vì dựng một địa chỉ giả. */
            <p className="text-[14.5px] leading-[1.55] text-[var(--text-secondary)]">
              Chưa có thông tin điểm gặp. Chuyên viên phụ trách sẽ báo trước buổi hẹn.
            </p>
          )}

          <button
            type="button"
            disabled
            title="Chưa có toạ độ điểm gặp"
            className="mt-5 inline-flex min-h-10 cursor-not-allowed items-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-subtle)] px-3.5 text-[13.5px] font-medium text-[var(--text-muted)] opacity-50"
          >
            <Navigation className="h-3.5 w-3.5" strokeWidth={2.2} aria-hidden />
            Chỉ đường
          </button>
        </Block>

        {contactName && (
          <Block title="Chuyên viên phụ trách">
            <p className="text-[17px] font-semibold leading-[1.3] text-[var(--text-primary)]">
              {contactName}
            </p>
            {phone && (
              <p className="mt-1.5 font-mono text-[15px] tabular-nums text-[var(--text-secondary)]">
                {phone}
              </p>
            )}

            {phone && (
              <div className="mt-5 flex flex-col gap-2">
                <a
                  href={`tel:${phone.replace(/[^\d+]/g, '')}`}
                  className="press inline-flex min-h-11 items-center justify-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-strong)] text-[14px] font-semibold text-[var(--text-primary)] transition-colors hover:border-[var(--agent)]"
                >
                  <Phone className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                  Gọi
                </a>
                <a
                  href={`sms:${phone.replace(/[^\d+]/g, '')}`}
                  className="press inline-flex min-h-11 items-center justify-center gap-2 rounded-[var(--r-sm)] border border-[var(--border-subtle)] text-[14px] font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  <MessageCircle className="h-4 w-4" strokeWidth={2.2} aria-hidden />
                  Nhắn tin
                </a>
              </div>
            )}
          </Block>
        )}

        {/* Nhãn lạ vẫn hiện — thêm nghiệp vụ mới không phải sửa file này. */}
        {details.some(
          (detail) =>
            !CONTACT_LABELS.has(detail.label) &&
            !PLACE_LABELS.has(detail.label) &&
            !schedule.includes(detail),
        ) && (
          <Block title="Khác">
            <dl className="space-y-4">
              {details
                .filter(
                  (detail) =>
                    !CONTACT_LABELS.has(detail.label) &&
                    !PLACE_LABELS.has(detail.label) &&
                    !schedule.includes(detail),
                )
                .map((detail) => (
                  <Field key={detail.label} label={detail.label} value={detail.value} />
                ))}
            </dl>
          </Block>
        )}

      </div>
    </div>
  )
}
