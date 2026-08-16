import {
  AlertTriangle,
  Check,
  Circle,
  CircleSlash,
  Hourglass,
  Loader2,
  UserRound,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { StepState } from '../../lib/journeyMock'

/**
 * Ngữ nghĩa trạng thái.
 *
 * `token` trỏ tới một VAI TRÒ ngữ nghĩa (`--running`, `--waiting-user`…) chứ
 * không phải một mã màu. Sáng và tối remap vai trò đó ở tầng token, nên
 * component này không biết mình đang ở theme nào — và không cần biết.
 *
 * Trạng thái được phân biệt bằng BỐN thứ độc lập: dấu hình học · nhãn chữ ·
 * độ nổi của bề mặt · chuyển động. Bỏ hết màu đi thì thứ tự ưu tiên vẫn đọc
 * được — đó là phép thử.
 */
export interface StepStateView {
  label: string
  Icon: LucideIcon
  spin?: boolean
  /** Vai trò ngữ nghĩa, không phải màu. */
  token: string
  /** 'focus' hút mắt · 'normal' rõ · 'quiet' lùi lại. */
  presence: 'focus' | 'normal' | 'quiet'
  /** Dải trạng thái có vệt quét — chỉ khi thật sự đang chạy. */
  scan?: boolean
  /** Dấu hình học vẽ trên dải: đầy · rỗng · nét đứt. */
  mark: 'solid' | 'hollow' | 'dashed'
}

export const STEP_STATE: Record<StepState, StepStateView> = {
  running: {
    label: 'Đang thực hiện',
    Icon: Loader2,
    spin: true,
    token: 'var(--running)',
    presence: 'focus',
    scan: true,
    mark: 'solid',
  },
  waiting_user: {
    label: 'Chờ bạn',
    Icon: UserRound,
    token: 'var(--waiting-user)',
    presence: 'focus',
    mark: 'solid',
  },
  failed: {
    label: 'Chưa thực hiện được',
    Icon: AlertTriangle,
    token: 'var(--danger)',
    presence: 'normal',
    mark: 'solid',
  },
  waiting_provider: {
    label: 'Chờ đơn vị',
    Icon: Hourglass,
    token: 'var(--waiting-provider)',
    presence: 'normal',
    mark: 'hollow',
  },
  success: {
    label: 'Hoàn tất',
    Icon: Check,
    token: 'var(--success)',
    presence: 'quiet',
    mark: 'solid',
  },
  proposed: {
    label: 'Chưa bắt đầu',
    Icon: Circle,
    token: 'var(--text-muted)',
    presence: 'quiet',
    mark: 'dashed',
  },
  skipped: {
    label: 'Không chạy',
    Icon: CircleSlash,
    token: 'var(--text-muted)',
    presence: 'quiet',
    mark: 'dashed',
  },
}
