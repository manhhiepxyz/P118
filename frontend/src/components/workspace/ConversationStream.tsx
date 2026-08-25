import { useEffect, useRef } from 'react'

import type { ChatTurn } from '../../lib/journeyMock'

/**
 * Hội thoại với P-118 — nằm giữa canvas và ô nhập, KHÔNG ở cột phải.
 *
 * Một lời nhắn của agent và ô để trả lời nó phải nằm cạnh nhau. Đặt hội thoại
 * sang cột phải thì người dùng đọc ở một chỗ rồi phải đưa mắt sang chỗ khác để
 * gõ — và cột phải biến thành một chatbot thứ hai, tranh việc với chính ô nhập
 * ngay bên dưới.
 *
 * Vì vậy cột phải chỉ giữ TÓM TẮT CÓ CẤU TRÚC và nút bấm nhanh; mọi câu chữ
 * đều ở đây.
 *
 * Không có bong bóng chat: đây là một dòng làm việc, không phải ứng dụng nhắn
 * tin. Người nói được phân biệt bằng canh lề và nhãn, không bằng hai khối màu.
 */
export function ConversationStream({
  turns,
  thinking = false,
}: {
  turns: ChatTurn[]
  /** P-118 đang soạn câu trả lời — hiện nhịp chấm thay vì im lặng. */
  thinking?: boolean
}) {
  const end = useRef<HTMLDivElement>(null)

  // Luôn nhìn thấy lượt mới nhất. `block: 'nearest'` để nó không kéo cả trang.
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [turns, thinking])

  if (turns.length === 0 && !thinking) return null

  return (
    <div className="shrink-0">
      <div
        className="mx-auto w-full max-w-[1000px] overflow-y-auto px-12"
        style={{ maxHeight: '30vh' }}
        aria-live="polite"
        aria-label="Trao đổi với P-118"
      >
        {/* `data-turn` là NEO CHO KIỂM THỬ, và nó tồn tại vì một lý do cụ thể.
            Harness chấp nhận từng bám vào class Tailwind
            (`div.flex.justify-end > p`, `div.flex.flex-col.items-start > p`) và
            đã gãy HAI LẦN khi bố cục đổi — lần gần nhất nó treo 30 giây rồi
            giết cả lượt chạy, trong khi sản phẩm hoàn toàn đúng: câu trả lời
            vẫn hiện trên màn hình, chỉ là ở một cấu trúc DOM khác.

            Một lượt kiểm thử báo đỏ vì lớp trình bày đổi là tiếng ồn; nó dạy
            người đọc phớt lờ kết quả. Thuộc tính này là hợp đồng công khai với
            harness, đổi bố cục thoải mái mà không phá nó. */}
        <ol className="space-y-3.5 py-3" data-chat-transcript>
          {turns.map((turn) =>
            turn.from === 'agent' ? (
              <li key={turn.id} className="rise flex gap-3" data-turn="agent">
                <span
                  aria-hidden
                  className="mt-[3px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[var(--r-xs)] font-mono text-[11px] font-bold"
                  style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
                >
                  P
                </span>
                <div className="min-w-0 flex-1">
                  <span className="sr-only">P-118: </span>
                  {/* `whitespace-pre-line` để giữ khoảng ngắt đoạn backend đặt
                      — câu hỏi tách khỏi phần dữ kiện thì dễ đọc hơn hẳn. */}
                  <p className="whitespace-pre-line text-[15px] leading-[1.6] text-[var(--text-primary)]">
                    {turn.text}
                  </p>
                </div>
              </li>
            ) : (
              <li key={turn.id} className="rise flex justify-end" data-turn="user">
                <span className="sr-only">Bạn: </span>
                <p
                  className="max-w-[75%] rounded-[var(--r-sm)] px-3.5 py-2 text-[15px] leading-[1.55] text-[var(--text-secondary)]"
                  style={{ backgroundColor: 'var(--surface-raised)' }}
                >
                  {turn.text}
                </p>
              </li>
            ),
          )}
          {thinking && (
            <li className="flex gap-3" aria-label="P-118 đang soạn câu trả lời">
              <span
                aria-hidden
                className="mt-[3px] flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[var(--r-xs)] font-mono text-[11px] font-bold"
                style={{ backgroundColor: 'var(--agent)', color: 'var(--surface-base)' }}
              >
                P
              </span>
              {/* Ba chấm lệch pha — nói "đang nghĩ" mà không hứa một khoảng
                  thời gian cụ thể như thanh tiến độ vẫn làm. */}
              <span className="mt-[7px] flex items-center gap-1" aria-hidden>
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="think-dot h-[5px] w-[5px] rounded-full"
                    style={{ backgroundColor: 'var(--text-muted)', animationDelay: `${i * 160}ms` }}
                  />
                ))}
              </span>
            </li>
          )}
        </ol>
        <div ref={end} />
      </div>
    </div>
  )
}
