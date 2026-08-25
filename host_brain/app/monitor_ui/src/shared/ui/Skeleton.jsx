
/**
 * 通用 Skeleton 骨架屏组件。
 * 用法：
 *   <Skeleton className="h-4 w-32" />           — 单行文字骨架
 *   <Skeleton className="h-24 w-full rounded-lg" /> — 卡片骨架
 *   <SkeletonCard />                             — 统计卡片骨架
 *   <SkeletonTable rows={5} cols={4} />          — 表格骨架
 */

export function Skeleton({ className = '' }) {
  return (
    <div
      className={`animate-pulse rounded bg-gray-200 ${className}`}
      aria-hidden="true"
    />
  )
}

export function SkeletonCard() {
  return (
    <div className="card p-4 space-y-3" aria-hidden="true">
      <Skeleton className="h-8 w-16 mx-auto" />
      <Skeleton className="h-3 w-20 mx-auto" />
    </div>
  )
}

export function SkeletonTable({ rows = 5, cols = 4 }) {
  return (
    <div className="card overflow-hidden" aria-hidden="true">
      {/* 表头 */}
      <div className="flex gap-4 px-4 py-3 bg-gray-50 border-b border-gray-200">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={`th-${i}`} className="h-3 flex-1" />
        ))}
      </div>
      {/* 行 */}
      {Array.from({ length: rows }).map((_, r) => (
        <div key={`tr-${r}`} className="flex gap-4 px-4 py-3 border-b border-gray-100">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={`td-${r}-${c}`} className="h-3 flex-1" />
          ))}
        </div>
      ))}
    </div>
  )
}

export function SkeletonServiceCards({ count = 6 }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3 sm:gap-4" aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div key={`svc-${i}`} className="card p-4 border-l-4 border-l-gray-200 space-y-2">
          <div className="flex items-center justify-between">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-5 w-12 rounded-full" />
          </div>
          <Skeleton className="h-3 w-16" />
        </div>
      ))}
    </div>
  )
}
