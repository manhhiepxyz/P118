import { useCallback, useEffect, useRef, useState } from 'react'
import { BadgeCheck, Clock, Home, Lock, Loader2, ShieldX } from 'lucide-react'

import { ChatWorkflowCard } from '../components/ChatWorkflowCard'
import { Composer } from '../components/Composer'
import { QuickActionForm } from '../components/QuickActionForm'
import {
  cancelWorkflow,
  continueWorkflow,
  getCapabilities,
  startWorkflow,
  type InitialWorkflowFormFields,
} from '../lib/agentApi'
import { useAuth } from '../lib/auth'
import type {
  AgentWorkflowResponse,
  Capability,
  ResidentLinkStatus,
} from '../lib/types'

/**
 * Trang chủ — MỘT cuộc hội thoại mới cho mỗi lần mở trang.
 *
 * Trước đây: gõ mục tiêu → `POST /start` → `navigate("/workflow/{id}")`. Người
 * dùng bị đẩy sang một trang khác ngay sau khi bấm gửi, và từ đó câu hỏi bổ
 * sung, tiến trình, bước duyệt thanh toán đều nằm tách khỏi thứ họ vừa gõ.
 * Không có chỗ nào để hỏi tiếp, và cũng không có gì nối hai màn hình lại.
 *
 * Mọi thứ của lượt hiện tại ở lại đây: tin nhắn của người dùng, thẻ workflow
 * sống, form bổ sung, card báo giá, rồi câu trả lời của P-118. Reload không
 * dựng lại lịch sử vào chat. Workflow đã lưu chỉ xuất hiện ở `/workflows`; khi
 * muốn tiếp tục, người dùng chủ động mở workflow đó.
 */

const LINK_VIEW: Record<ResidentLinkStatus, { label: string; hint: string; tone: string; Icon: typeof Home }> = {
  VERIFIED: {
    label: 'Đã xác minh căn hộ',
    hint: 'Bạn dùng được đầy đủ dịch vụ dành cho cư dân.',
    tone: 'border-teal-200 bg-teal-50 text-teal-900 dark:border-teal-900/50 dark:bg-teal-950/30 dark:text-teal-200',
    Icon: BadgeCheck,
  },
  PENDING: {
    label: 'Hồ sơ đang chờ duyệt',
    hint: 'Ban quản lý đang xem xét. Dịch vụ cư dân sẽ mở sau khi được duyệt.',
    tone: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200',
    Icon: Clock,
  },
  REJECTED: {
    label: 'Hồ sơ chưa được duyệt',
    hint: 'Vui lòng liên hệ ban quản lý toà nhà để được hỗ trợ.',
    tone: 'border-red-200 bg-red-50 text-red-900 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-200',
    Icon: ShieldX,
  },
  NOT_LINKED: {
    label: 'Chưa liên kết căn hộ',
    hint: 'Gửi yêu cầu ở mục "Liên kết căn hộ" để ban quản lý xác minh. Việc xác minh do ban quản lý thực hiện, không tự khai được.',
    tone: 'border-gray-200 bg-gray-50 text-gray-700 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300',
    Icon: Home,
  },
}

type ChatMessage =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'assistant'; id: string; text: string; suggestions: string[] }
  | { kind: 'thinking'; id: string }
  | { kind: 'workflow'; id: string; workflowId: string; goal: string }

let messageCounter = 0
const nextId = () => `m${++messageCounter}`

const CANCELLABLE_STATUSES = new Set([
  'PENDING',
  'RUNNING',
  'NEEDS_INFORMATION',
  'WAITING_APPROVAL',
])

/**
 * Chỉ nhận câu điều khiển độc lập, không bắt nhầm câu như “không muốn huỷ”.
 * Đây là lệnh UI deterministic; không tốn một lượt Planner để hiểu một thao
 * tác đã có đích rõ ràng là workflow đang hoạt động.
 */
function isCancelCommand(text: string): boolean {
  const normalized = text
    .trim()
    .toLocaleLowerCase('vi-VN')
    .replace(/[.!?]+$/u, '')
    .replace(/\s+/gu, ' ')
  return /^(?:(?:tôi|mình)\s+)?(?:(?:muốn|cần)\s+)?(?:huỷ|hủy|dừng|bỏ)(?:\s+(?:yêu cầu|tác vụ|workflow|việc)(?:\s+(?:này|hiện tại))?)?$/u.test(
    normalized,
  )
}

interface WorkflowRuntimeState {
  currentWorkflowId: string
  data: AgentWorkflowResponse
}

export function HomePage() {
  const { user } = useAuth()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [goal, setGoal] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [quickActions, setQuickActions] = useState<Capability[]>([])
  const [workflowStates, setWorkflowStates] = useState<Record<string, WorkflowRuntimeState>>({})
  const [workflowSnapshots, setWorkflowSnapshots] = useState<
    Record<string, AgentWorkflowResponse>
  >({})
  const [workflowTargets, setWorkflowTargets] = useState<Record<string, string>>({})

  /** Chặn StrictMode/remount hoặc hai thẻ cùng workflow thêm lại một câu. */
  const announcedReplies = useRef<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    getCapabilities()
      .then((items) => {
        if (!cancelled) setCapabilities(items)
      })
      .catch(() => {
        /* Không chặn trang: ô nhập mục tiêu vẫn dùng được. */
      })
    return () => {
      cancelled = true
    }
  }, [])

  /** P-118 nói một câu khi workflow dừng lại — đúng một lần cho mỗi lần dừng. */
  const handleSettled = useCallback((data: AgentWorkflowResponse) => {
    // NEEDS_INFORMATION dùng `question`, còn workflow đã hoàn tất thường dùng
    // `answer`/`summary`. Bỏ `question` làm câu hỏi đầu tiên biến mất khỏi lịch
    // sử dù người dùng vẫn nhìn thấy trạng thái chờ trong card.
    const text = data.answer || data.summary || data.question || data.message
    if (!text) return
    const announcementKey = `${data.workflow_id ?? 'unknown'}:${data.status}:${text}`
    if (announcedReplies.current.has(announcementKey)) return
    announcedReplies.current.add(announcementKey)
    setMessages((prev) => {
      // Child workflow có thể diễn đạt y hệt parent. Không để hai bubble giống
      // hệt đứng cạnh nhau dù chúng mang hai workflow_id khác nhau.
      const previousReply = [...prev].reverse().find((message) => message.kind === 'assistant')
      if (previousReply?.kind === 'assistant' && previousReply.text.trim() === text.trim()) return prev
      return [
        ...prev,
        {
          kind: 'assistant',
          id: nextId(),
          text,
          // Capability tìm kiếm bất động sản đã bị gỡ khỏi trải nghiệm người
          // dùng. Lọc cả snapshot cũ để một workflow đã lưu không làm nút này
          // xuất hiện lại sau khi backend catalogue đã đổi.
          suggestions: (data.suggestions ?? []).filter(
            (suggestion) => !suggestion.toLocaleLowerCase('vi-VN').includes('gợi ý bất động sản'),
          ),
        },
      ]
    })
  }, [])

  const handleWorkflowState = useCallback(
    (rootWorkflowId: string, currentWorkflowId: string, data: AgentWorkflowResponse) => {
      setWorkflowStates((previous) => {
        const current = previous[rootWorkflowId]
        if (current?.currentWorkflowId === currentWorkflowId && current.data === data) return previous
        return { ...previous, [rootWorkflowId]: { currentWorkflowId, data } }
      })
    },
    [],
  )

  function latestCancellableWorkflow(): { rootWorkflowId: string; currentWorkflowId: string } | null {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.kind !== 'workflow') continue
      const runtime = workflowStates[message.workflowId]
      if (runtime && CANCELLABLE_STATUSES.has(runtime.data.status)) {
        return { rootWorkflowId: message.workflowId, currentWorkflowId: runtime.currentWorkflowId }
      }
    }
    return null
  }

  function latestWorkflowIn(statuses: Set<string>): {
    rootWorkflowId: string
    currentWorkflowId: string
  } | null {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.kind !== 'workflow') continue
      const runtime = workflowStates[message.workflowId]
      if (runtime && statuses.has(runtime.data.status)) {
        return { rootWorkflowId: message.workflowId, currentWorkflowId: runtime.currentWorkflowId }
      }
    }
    return null
  }

  /** Tạo workflow từ một goal hoàn chỉnh, bất kể goal đến từ chat hay quick form. */
  async function launchWorkflow(text: string, formFields?: InitialWorkflowFormFields) {
    setSending(true)
    setError(null)
    const thinkingId = nextId()
    setMessages((previous) => [
      ...previous,
      { kind: 'user', id: nextId(), text },
      { kind: 'thinking', id: thinkingId },
    ])

    try {
      // Browser chỉ gửi goal. TaskPlan, dependency, quyền và context đều do
      // backend quyết định; quick form không phải một workflow builder.
      const response = await startWorkflow(text, undefined, undefined, formFields)
      const created = response.workflow_id
      if (!created) throw new Error('Không tạo được yêu cầu. Vui lòng thử lại.')
      setMessages((previous) => [
        ...previous.filter((message) => message.id !== thinkingId),
        { kind: 'workflow', id: nextId(), workflowId: created, goal: text },
      ])
    } catch (reason) {
      setMessages((previous) => previous.filter((message) => message.id !== thinkingId))
      throw reason
    } finally {
      setSending(false)
    }
  }

  async function send() {
    const text = goal.trim()
    if (!text || sending) return

    if (isCancelCommand(text)) {
      const active = latestCancellableWorkflow()
      setGoal('')
      setError(null)
      setMessages((previous) => [...previous, { kind: 'user', id: nextId(), text }])

      if (!active) {
        setMessages((previous) => [
          ...previous,
          {
            kind: 'assistant',
            id: nextId(),
            text: 'Hiện không có yêu cầu nào đang chạy hoặc đang chờ bạn xử lý.',
            suggestions: [],
          },
        ])
        return
      }

      setSending(true)
      try {
        const cancelled = await cancelWorkflow(active.currentWorkflowId)
        // Đẩy thẳng response vào đúng thẻ. Form biến mất ngay, không phải chờ
        // polling; câu trả lời tự nhiên tiếp tục được Response Agent sinh nền.
        setWorkflowSnapshots((previous) => ({
          ...previous,
          [active.rootWorkflowId]: cancelled,
        }))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Không huỷ được yêu cầu.')
      } finally {
        setSending(false)
      }
      return
    }

    const waiting = latestWorkflowIn(new Set(['NEEDS_INFORMATION']))
    if (waiting) {
      setSending(true)
      setError(null)
      const thinkingId = nextId()
      setMessages((previous) => [
        ...previous,
        { kind: 'user', id: nextId(), text },
        { kind: 'thinking', id: thinkingId },
      ])
      try {
        const next = await continueWorkflow(waiting.currentWorkflowId, { message: text })
        setMessages((previous) => previous.filter((message) => message.id !== thinkingId))
        setWorkflowSnapshots((previous) => ({
          ...previous,
          [waiting.rootWorkflowId]: next,
        }))
        if (next.workflow_id && next.workflow_id !== waiting.currentWorkflowId) {
          setWorkflowTargets((previous) => ({
            ...previous,
            [waiting.rootWorkflowId]: next.workflow_id as string,
          }))
        } else if (next.answer || next.summary || next.message) {
          handleSettled(next)
        }
        setGoal('')
      } catch (e) {
        setMessages((previous) => previous.filter((message) => message.id !== thinkingId))
        setError(e instanceof Error ? e.message : 'Chưa gửi được câu trả lời.')
      } finally {
        setSending(false)
      }
      return
    }

    try {
      await launchWorkflow(text)
      setGoal('')
    } catch (e) {
      // GIỮ NGUYÊN nội dung đã gõ: bắt người dùng viết lại vì một lỗi mạng là
      // cách chắc chắn để họ bỏ cuộc.
      setError(e instanceof Error ? e.message : 'Đã xảy ra lỗi. Vui lòng thử lại.')
    }
  }

  // Xử lý phím Enter đã chuyển vào `Composer`, KỂ CẢ guard `isComposing` cho
  // bộ gõ tiếng Việt — thiếu nó thì gõ "cà" sẽ gửi mất chữ "ca".

  const status: ResidentLinkStatus = user?.resident_verification_status ?? 'NOT_LINKED'
  const view = LINK_VIEW[status] ?? LINK_VIEW.NOT_LINKED
  const StatusIcon = view.Icon
  const empty = messages.length === 0
  const waitingWorkflow = latestWorkflowIn(new Set(['NEEDS_INFORMATION']))
  const waitingSnapshot = waitingWorkflow
    ? workflowStates[waitingWorkflow.rootWorkflowId]?.data
    : null
  // Backend công bố NEEDS_INFORMATION trước rồi mới sinh lời giải thích tự
  // nhiên. Không mở composer trong cửa sổ này: một /continue gửi quá sớm có
  // thể thay snapshot và làm mất chính câu hỏi P-118 đang soạn.
  const assistantPreparing = waitingSnapshot?.response_state === 'PENDING'
  const waitingForReply = waitingWorkflow && !assistantPreparing
  const workflowRunning = latestWorkflowIn(new Set(['PENDING', 'RUNNING']))

  return (
    <div className="flex min-h-[calc(100vh-9rem)] flex-col gap-4">
      <header>
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">
          Chào {user?.username ?? 'bạn'}
        </h1>
        <p className="mt-1 text-sm text-gray-500">P-118 có thể giúp bạn việc gì hôm nay?</p>
      </header>

      <section className={`rounded-2xl border p-4 ${view.tone}`}>
        <div className="flex items-start gap-3">
          <StatusIcon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-semibold">{view.label}</p>
            <p className="mt-1 text-sm opacity-90">{view.hint}</p>
            {/* Căn hộ chỉ hiện khi ĐÃ xác minh — hiện sớm hơn là khẳng định một
                quan hệ sở hữu mà hệ thống chưa xác nhận. */}
            {status === 'VERIFIED' && user?.apartment_code && (
              <p className="mt-2 text-sm font-medium">
                {user.apartment_code}
                {user.residential_area ? ` · ${user.residential_area}` : ''}
              </p>
            )}
          </div>
        </div>
      </section>

      {/* Cuộc hội thoại */}
      <div
        className="flex-1 space-y-4 overflow-y-auto rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800"
        aria-live="polite"
        aria-label="Cuộc hội thoại với P-118"
      >
        {empty && (
          <p className="py-8 text-center text-sm text-gray-500">
            Mô tả việc bạn cần, hoặc chọn một dịch vụ bên dưới. Mình sẽ lên kế hoạch và làm cùng bạn
            ngay tại đây. Các yêu cầu trước nằm trong mục Workflows.
          </p>
        )}

        {messages.map((message) => {
          if (message.kind === 'user') {
            return (
              <div key={message.id} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-teal-700 px-4 py-2.5 text-sm text-white">
                  {message.text}
                </p>
              </div>
            )
          }
          if (message.kind === 'thinking') {
            return (
              <div key={message.id} className="flex justify-start">
                <p className="inline-flex items-center gap-2 rounded-2xl rounded-bl-sm bg-gray-100 px-4 py-2.5 text-sm text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  P-118 đang xem thông tin còn thiếu…
                </p>
              </div>
            )
          }
          if (message.kind === 'assistant') {
            return (
              <div key={message.id} className="flex flex-col items-start gap-2">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-gray-100 px-4 py-2.5 text-sm text-gray-800 dark:bg-gray-800 dark:text-gray-100">
                  {message.text}
                </p>
                {message.suggestions.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {message.suggestions.map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => {
                          setGoal(suggestion)
                        }}
                        className="rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-700 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700 dark:text-gray-300"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
          }
          return (
            <div key={message.id} className="max-w-[95%]">
              <ChatWorkflowCard
                workflowId={message.workflowId}
                goal={message.goal}
                onSettled={handleSettled}
                externalSnapshot={workflowSnapshots[message.workflowId]}
                externalCurrentId={workflowTargets[message.workflowId]}
                onStateChange={handleWorkflowState}
              />
            </div>
          )
        })}
      </div>

      {/* Composer ghim dưới cùng */}
      <section className="rounded-2xl border border-gray-200 bg-card p-4 dark:border-gray-800">
        <div className="mb-2 flex items-center justify-between gap-2">
          <p className="text-xs text-gray-500">
            {waitingForReply
              ? 'P-118 đang chờ câu trả lời cho yêu cầu hiện tại.'
              : assistantPreparing
                ? 'P-118 đang xem thông tin còn thiếu…'
              : workflowRunning
                ? 'P-118 đang thực hiện yêu cầu. Bạn có thể gửi tiếp khi bước hiện tại dừng.'
                : 'Mô tả một yêu cầu mới cho P-118.'}
          </p>
          {goal.trim() && (
            <button
              type="button"
              onClick={() => {
                setGoal('')
                setError(null)
              }}
              className="shrink-0 rounded-full border border-gray-300 px-3 py-1 text-xs text-gray-600 hover:border-teal-700 hover:text-teal-700 dark:border-gray-700 dark:text-gray-300"
            >
              Tạo yêu cầu mới
            </button>
          )}
        </div>
        {/* Ô nhập dính đáy. Logic gửi/huỷ/poll GIỮ NGUYÊN — chỉ đổi vỏ.
            `id="goal"` giữ nguyên: browser E2E điền vào `#goal` và còn chờ nó
            `state: 'visible'`, nên nó phải là ô nhập THẬT, không phải input ẩn. */}
        <Composer
          id="goal"
          value={goal}
          onChange={setGoal}
          onSubmit={() => void send()}
          disabled={sending || Boolean(workflowRunning) || assistantPreparing}
          placeholder="Ví dụ: Đặt lịch tham quan Ocean Park ngày 20/09 lúc 10:00"
          hint={waitingForReply ? 'P-118 đang chờ câu trả lời cho yêu cầu hiện tại.' : null}
          submitLabel={waitingForReply ? 'Gửi' : 'Bắt đầu'}
        />
        {error && (
          <p className="mt-2 text-sm text-red-600" role="alert">
            {error}
          </p>
        )}
      </section>

      {capabilities.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Dịch vụ</h2>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {capabilities.map((capability) => {
              const selected = quickActions.some((item) => item.name === capability.name)
              return (
              <button
                key={capability.name}
                type="button"
                // Backend quyết định mở hay khoá. Frontend KHÔNG tự suy từ role.
                disabled={!capability.available || sending}
                // Multi-select: mỗi click thêm/bỏ một dịch vụ. Chưa tạo
                // workflow và chưa gọi model cho tới khi gửi form tổng hợp.
                onClick={() => {
                  setQuickActions((previous) =>
                    selected
                      ? previous.filter((item) => item.name !== capability.name)
                      : [...previous, capability],
                  )
                }}
                aria-pressed={selected}
                aria-expanded={selected}
                title={capability.blocked_reason ?? undefined}
                className={`rounded-2xl border p-4 text-left transition ${
                  !capability.available
                    ? 'cursor-not-allowed border-gray-200 bg-gray-50 opacity-70 dark:border-gray-800 dark:bg-gray-900'
                    : selected
                      ? 'border-teal-700 bg-teal-50 dark:border-teal-700 dark:bg-teal-950/30'
                      : 'border-gray-200 bg-card hover:border-teal-700/50 dark:border-gray-800'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{capability.name}</p>
                  {!capability.available ? (
                    <Lock className="h-4 w-4 shrink-0 text-gray-400" aria-hidden />
                  ) : null}
                </div>
                <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">{capability.description}</p>
                {/* Dịch vụ bị khoá vẫn HIỆN, kèm lý do: ẩn hẳn thì người dùng
                    không biết nó tồn tại và cũng không biết cần làm gì để mở. */}
                {!capability.available && capability.blocked_reason && (
                  <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">{capability.blocked_reason}</p>
                )}
              </button>
              )
            })}
          </div>
          {quickActions.length > 0 && (
            <QuickActionForm
              capabilities={quickActions}
              submitting={sending}
              onCancel={() => setQuickActions([])}
              onSubmit={async (preparedGoal, formFields) => {
                await launchWorkflow(preparedGoal, formFields)
                setQuickActions([])
              }}
            />
          )}
        </section>
      )}
    </div>
  )
}
