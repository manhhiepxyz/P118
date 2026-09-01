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
  stage = null,
  fill = false,
}: {
  turns: ChatTurn[]
  /** Khung cha đã cho chiều cao thật — thôi tự đặt trần `30vh`. */
  fill?: boolean
  /** P-118 đang soạn câu trả lời — hiện nhịp chấm thay vì im lặng. */
  thinking?: boolean
  /**
   * Việc backend ĐANG làm, lấy từ sự kiện mới nhất.
   *
   * Ba chấm nói "đang nghĩ" nhưng không nói nghĩ gì, và lượt lập kế hoạch có
   * thể kéo 20–120 giây. Ba chấm im lặng suốt hai phút đọc lên như đã treo.
   *
   * Backend PHÁT sẵn chuỗi này từ giây đầu — PLANNING → PLANNED → VALIDATING →
   * VALIDATED → EXECUTING — nhưng workspace chưa bao giờ dùng tới.
   */
  stage?: string | null
}) {
  const end = useRef<HTMLDivElement>(null)

  // Luôn nhìn thấy lượt mới nhất. `block: 'nearest'` để nó không kéo cả trang.
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [turns, thinking])

  if (turns.length === 0 && !thinking) return null

  return (
    /* `fill`: khung cha đã cho chiều cao thật (chế độ hành trình có thanh chia
       kéo được), nên ở đây KHÔNG tự đặt trần nữa.

       Trần cứng `30vh` là thứ đã làm hội thoại "bị cắt ngang": nó giới hạn bất
       kể khung cha rộng bao nhiêu, nên kéo thanh chia xuống cũng không thêm
       được dòng nào — đo được vùng cuộn 134px nằm trong khung 253px, để lại
       130px trống chết ngay trên ô soạn thảo.

       `justify-end` để ít tin nhắn thì chúng nằm SÁT ô soạn thảo thay vì lơ
       lửng trên đỉnh; nhiều tin nhắn thì nó cuộn như thường. */
    <div className={fill ? 'flex min-h-0 flex-1 flex-col' : 'shrink-0'}>
      <div
        /* `mt-auto` trên danh sách, KHÔNG `justify-end` trên khung cuộn.
           Hai cách trông giống nhau khi ít tin nhắn, nhưng khi nội dung tràn thì
           `justify-content: flex-end` cắt mất phần ĐẦU và không cuộn ngược lên
           được — đúng lỗi vừa báo: "khung chat không lướt lên được".
           `margin-top: auto` đẩy nội dung xuống đáy y hệt mà vẫn cuộn đủ. */
        className={`mx-auto flex w-full max-w-[1000px] flex-col overflow-y-auto px-12${
          fill ? ' min-h-0 flex-1' : ''
        }`}
        style={fill ? undefined : { maxHeight: '30vh' }}
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
        <ol className="mt-auto space-y-3.5 py-3" data-chat-transcript>
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
              {/* Câu VIỆC ĐANG LÀM đứng trước ba chấm khi có.
                  Ba chấm giữ lại chứ không thay: chúng nói "vẫn đang chạy"
                  giữa hai lần đổi giai đoạn, mà một giai đoạn có thể đứng yên
                  cả phút. Chỉ có chữ thì màn hình lại trông như đã dừng. */}
              <span className="mt-[4px] flex items-center gap-2">
                {stage && (
                  <span className="text-[14.5px] leading-[1.6] text-[var(--text-secondary)]">{stage}</span>
                )}
                <span className="flex items-center gap-1" aria-hidden>
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="think-dot h-[5px] w-[5px] rounded-full"
                      style={{ backgroundColor: 'var(--text-muted)', animationDelay: `${i * 160}ms` }}
                    />
                  ))}
                </span>
              </span>
            </li>
          )}
        </ol>
        <div ref={end} />
      </div>
    </div>
  )
}
