import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router-dom'

import { StatusBadge } from '../StatusBadge'
import type { AgentTaskResult } from '../../lib/types'
import { JourneyStepList } from './JourneyStepList'

interface Props {
  workflowId: string
  title: string
  status: string
  tasks: AgentTaskResult[]
  /** Gập = chỉ tiêu đề + tiến độ. Dùng ở danh sách và trên mobile. */
  collapsed?: boolean
  /** Nội dung chèn dưới danh sách bước: cổng chờ, câu hỏi bổ sung, xem trước… */
  children?: React.ReactNode
  footer?: React.ReactNode
}

/**
 * Một hành trình — đơn vị tổ chức chính của sản phẩm.
 *
 * Người dùng nghĩ "chuyến xem nhà ngày 20/09", không nghĩ "workflow f9be7d31".
 * Thẻ này vì thế lấy MỤC TIÊU làm tiêu đề và các bước làm nội dung, thay vì
 * trình bày theo cấu trúc kỹ thuật của backend.
 *
 * Ở chế độ gập chỉ còn tiêu đề, tiến độ và trạng thái: đủ để quét một danh
 * sách dài mà không phải cuộn qua chi tiết của từng việc.
 */
export function JourneyCard({
  workflowId,
  title,
  status,
  tasks,
  collapsed = false,
  children,
  footer,
}: Props) {
  const done = tasks.filter((task) => task.status === 'SUCCESS').length
  const total = tasks.length

  return (
    <article className="rounded-2xl border border-gray-200 bg-card p-4 shadow-sm transition dark:border-gray-700">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {/* `break-words`: tiêu đề là câu người dùng gõ, có thể rất dài. */}
            <span className="break-words">{title}</span>
          </h2>
          {total > 0 && (
            <p className="mt-1 text-xs text-gray-500 tabular-nums dark:text-gray-400">
              {done}/{total} bước
            </p>
          )}
        </div>
        <StatusBadge status={status} />
      </header>

      {total > 0 && (
        <div className="mt-3">
          {/* Thanh tiến độ chỉ để LIẾC — số bước ở trên mới là thông tin chính. */}
          <div className="h-1 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
            <div
              className="h-full rounded-full bg-brand-600 transition-[width] duration-500 dark:bg-teal-400"
              style={{ width: `${total ? (done / total) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {!collapsed && total > 0 && (
        <div className="mt-4">
          <JourneyStepList tasks={tasks} />
        </div>
      )}

      {children && <div className="mt-4">{children}</div>}

      <footer className="mt-4 flex items-center justify-between gap-3">
        <Link
          to={`/workflow/${workflowId}`}
          className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 hover:underline dark:text-teal-400"
        >
          {collapsed ? 'Mở hành trình' : 'Xem chi tiết'}
          <ChevronRight className="h-4 w-4" aria-hidden />
        </Link>
        {footer}
      </footer>
    </article>
  )
}
