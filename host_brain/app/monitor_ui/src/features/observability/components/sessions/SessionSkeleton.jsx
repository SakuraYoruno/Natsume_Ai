export default function SessionSkeleton() {
  return (
    <div className="grid gap-3 xl:grid-cols-2" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, index) => (
        <div key={index} className="rounded-2xl border border-gray-200 bg-white/85 p-4">
          <div className="h-3 w-3/4 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-3 h-3 w-1/2 animate-pulse rounded-full bg-gray-200" />
          <div className="mt-4 h-12 w-full animate-pulse rounded-xl bg-gray-100" />
          <div className="mt-3 flex gap-2">
            <div className="h-6 w-24 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-20 animate-pulse rounded-full bg-gray-100" />
            <div className="h-6 w-28 animate-pulse rounded-full bg-gray-100" />
          </div>
        </div>
      ))}
    </div>
  )
}
