import { ArrowDown } from 'lucide-react'

/** Skeleton cards cho loading — theo spec Home §2.1.3. */
export function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-2xl border border-gray-200 bg-card p-4">
      <div className="h-4 w-24 rounded bg-gray-200" />
      <div className="mt-3 h-4 w-3/4 rounded bg-gray-200" />
      <div className="mt-2 h-3 w-1/2 rounded bg-gray-100" />
    </div>
  )
}

export function SkeletonRows({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  )
}

/** EmptyState — trạng thái trống của danh sách workflow. */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-gray-300 bg-card px-6 py-10 text-center">
      <p className="text-sm text-gray-500">{message}</p>
    </div>
  )
}

/** Mũi tên dữ liệu giữa 2 node timeline: "resident_id → T2". */
export function DataPropagation({ label }: { label: string }) {
  return (
    <div className="my-1 flex items-center gap-1.5 pl-10 text-xs text-gray-400">
      <ArrowDown className="h-3.5 w-3.5" aria-hidden />
      <span className="font-mono">{label}</span>
    </div>
  )
}

/** Dòng UX "Agent đang tự động lập lại kế hoạch" — Dynamic Replanning. */
export function ReplanningNotice() {
  return (
    <div className="mt-2 flex items-center gap-2 rounded-xl border border-amber-300 bg-amber-50/90 px-4 py-3 text-xs font-semibold text-amber-800 shadow-sm dark:border-amber-700/60 dark:bg-amber-950/50 dark:text-amber-300">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-200 text-amber-800 dark:bg-amber-900 dark:text-amber-200">
        🔄
      </span>
      <p>
        <span className="font-bold">Dynamic Replanning:</span> AI Agent đã tự động phát hiện sự cố, giữ lại các bước thành công và đang lập phương án thay thế mới…
      </p>
    </div>
  )
}
