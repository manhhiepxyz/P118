import { useLayoutEffect, useRef } from 'react'
import { ArrowUp, Loader2, Sparkles, X } from 'lucide-react'

interface Props {
  mode: 'launcher' | 'journey'
  selected: string[]
  onRemove: (name: string) => void
  value: string
  onChange: (value: string) => void
  onExecute: () => void
  journeyLabel?: string
  /** Đang gửi / đang chuyển cảnh — KHOÁ nút, không phải trạng thái hệ thống. */
  busy?: boolean
  /**
   * P-118 đang thật sự làm việc: workflow còn chạy, hoặc câu trả lời đang soạn.
   *
   * Tách khỏi `busy` một cách có chủ ý. Trước đây chỉ báo trạng thái đọc `busy`,
   * mà `busy` được truyền vào là `leaving` — cờ HOẠT ẢNH của vùng năng lực. Nên
   * dòng "Đang thực hiện" chỉ nháy trong một transition CSS và chưa bao giờ
   * hiện lúc hệ thống thật sự chạy.
   *
   * Cũng KHÔNG dùng chung với `busy` để khoá nút: người dùng phải hỏi được
   * giữa chừng trong lúc một workflow đang chờ thông tin.
   */
  working?: boolean
  /** Lý do lần bấm vừa rồi không chạy. Hiện ngay cạnh chỉ báo trạng thái. */
  notice?: string | null
}

/** Trần chiều cao của ô nhập, tính bằng px. Khoảng 6 dòng. */
const MAX_INPUT_HEIGHT = 148

/**
 * Bảng lệnh của P-118 — mép dưới của sân khấu, KHÔNG phải một tấm nổi riêng.
 *
 * Bản trước là `[ô nhập trắng] [nút teal to]`. Hai phần tử ấy đúng chức năng
 * nhưng sai vật liệu: một hộp trắng tinh và một khối màu bão hoà đọc như form
 * web dán vào phần mềm, chứ không phải một bộ phận của nó.
 *
 * Bản này gom cả hai vào MỘT bề mặt: ô nhập gần như trong suốt nằm trên nền
 * của bảng, chip ngữ cảnh và nút chạy ở hàng chân cùng bảng. Viền và vệt sáng
 * mép trên làm việc phân tách, không phải màu nền. Nhờ vậy nền → bảng → ô nhập
 * đọc như ba độ sâu của một vật liệu:
 *
 *     nền sân khấu  →  bề mặt tương tác  →  chỗ đang gõ
 *
 * Nó vẫn nằm trong luồng bố cục bình thường và cao theo nội dung, nên không
 * bao giờ che mất workspace.
 */
export function CommandRail({
  mode,
  selected,
  onRemove,
  value,
  onChange,
  onExecute,
  journeyLabel,
  busy = false,
  working = false,
  notice = null,
}: Props) {
  const canRun = (selected.length > 0 || value.trim().length > 0) && !busy
  const input = useRef<HTMLTextAreaElement>(null)

  // Cao theo nội dung tới một trần. Đo lại từ `auto` mỗi lượt, nếu không chiều
  // cao chỉ tăng được chứ không co lại khi người dùng xoá bớt chữ.
  useLayoutEffect(() => {
    const node = input.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(node.scrollHeight, MAX_INPUT_HEIGHT)}px`
    node.style.overflowY = node.scrollHeight > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
  }, [value])

  return (
    <div className="relative shrink-0">
      {/* Vệt chuyển màu: nội dung cuộn tan dần vào mép dưới. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-10 h-10"
        style={{ background: 'linear-gradient(to bottom, transparent, var(--surface-base))' }}
      />

      <div className="mx-auto w-full max-w-[1000px] px-12 pb-6 pt-1">
        {/* Chỉ báo trạng thái — CHỈ hiện khi có gì để nói.
            Trước đây dòng này luôn hiện, và ở trạng thái rảnh nó đọc là
            "P-118 · Sẵn sàng" — trạng thái rỗng, chiếm một dòng ngay trên ô
            nhập để khẳng định rằng không có gì đang xảy ra. Người dùng học cách
            bỏ qua nó, và khi nó chuyển thành một thông báo lỗi thật thì họ cũng
            bỏ qua nốt.
            Giữ đúng ba trường hợp có tín hiệu: đang chạy, có lỗi vừa xảy ra,
            hoặc đang mở một hành trình cụ thể. */}
        {(working || notice || journeyLabel) && (
          <div className="mb-2 flex items-center gap-2 px-0.5 text-[12px] text-[var(--text-muted)]">
            <span
              aria-hidden
              className={`h-[6px] w-[6px] shrink-0 rounded-full ${working ? 'pulse-dot' : ''}`}
              style={{
                backgroundColor: working ? 'var(--running)' : notice ? 'var(--danger)' : 'var(--agent)',
              }}
            />
            {notice && !working ? (
              // `role="alert"` để trình đọc màn hình đọc ngay: đây là phản hồi
              // cho một hành động vừa xảy ra, không phải chữ trang trí.
              <span role="alert" className="font-medium" style={{ color: 'var(--danger)' }}>
                {notice}
              </span>
            ) : working ? (
              <span>Đang thực hiện</span>
            ) : null}
            {journeyLabel && !notice && (
              <>
                {working && <span aria-hidden>·</span>}
                <span className="min-w-0 flex-1 truncate">{journeyLabel}</span>
              </>
            )}
          </div>
        )}

        {/* Bảng lệnh. `focus-within` nâng cả bảng chứ không riêng ô nhập —
            người ta đang gõ vào PHẦN MỀM, không phải vào một ô. */}
        <div className="console overflow-hidden rounded-[var(--r-md)]">
          {busy && <span aria-hidden className="console-progress" />}

          <div className="flex items-start gap-3 px-4 pt-3.5">
            <Sparkles
              aria-hidden
              className="mt-[3px] h-[17px] w-[17px] shrink-0"
              strokeWidth={1.8}
              style={{ color: canRun ? 'var(--agent)' : 'var(--text-muted)' }}
            />
            <label htmlFor="ws-composer" className="sr-only">
              Mô tả việc bạn muốn P-118 thực hiện
            </label>
            <textarea
              id="ws-composer"
              ref={input}
              rows={1}
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onKeyDown={(event) => {
                // Enter chạy, Shift+Enter xuống dòng. `isComposing` để bộ gõ
                // tiếng Việt không bị cướp phím Enter lúc đang chọn dấu.
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  if (canRun) onExecute()
                }
              }}
              placeholder={
                mode === 'journey'
                  ? 'Thêm việc, đổi giờ, hoặc hỏi P-118…'
                  : 'Mô tả thêm việc bạn muốn P-118 thực hiện…'
              }
              className="min-h-[26px] flex-1 resize-none bg-transparent text-[16px] leading-[1.45] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
            />
          </div>

          {/* Hàng chân: ngữ cảnh bên trái, hành động bên phải. Chip nằm TRONG
              bảng nên chọn thêm dịch vụ không đội bảng lên thành một khối mới. */}
          <div className="flex items-end gap-3 px-3 pb-3 pt-2.5">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
              {selected.map((name) => (
                <span
                  key={name}
                  className="rise inline-flex max-w-full items-center gap-1.5 rounded-[var(--r-xs)] py-1 pl-2.5 pr-1.5 text-[12.5px] font-medium"
                  style={{
                    color: 'var(--agent)',
                    backgroundColor: 'color-mix(in srgb, var(--agent) 11%, transparent)',
                    boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--agent) 24%, transparent)',
                  }}
                >
                  <span className="truncate">{name}</span>
                  <button
                    type="button"
                    onClick={() => onRemove(name)}
                    aria-label={`Bỏ ${name}`}
                    className="press cursor-pointer rounded-[2px] p-0.5 transition-opacity hover:opacity-60"
                  >
                    <X className="h-3 w-3" strokeWidth={2.8} aria-hidden />
                  </button>
                </span>
              ))}
              {selected.length === 0 && (
                <span className="truncate text-[12px] text-[var(--text-muted)]">
                  Enter để chạy · Shift+Enter xuống dòng
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={onExecute}
              disabled={!canRun}
              // Ở chế độ hành trình nút chỉ còn mũi tên — không có nhãn nhìn
              // thấy thì phải có tên đọc được, nếu không trình đọc màn hình
              // chỉ nghe thấy "button".
              aria-label={mode === 'launcher' ? 'Thực hiện' : 'Gửi cho P-118'}
              title={mode === 'launcher' ? 'Thực hiện' : 'Gửi cho P-118'}
              className="console-run press inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-[var(--r-sm)] px-3.5 text-[13.5px] font-semibold disabled:cursor-not-allowed"
              style={
                canRun
                  ? { backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }
                  : {
                      color: 'var(--text-muted)',
                      boxShadow: 'inset 0 0 0 1px var(--border-subtle)',
                    }
              }
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.4} aria-hidden />
              ) : (
                <ArrowUp className="h-4 w-4" strokeWidth={2.6} aria-hidden />
              )}
              {mode === 'launcher' && (busy ? 'Đang chạy' : 'Thực hiện')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
