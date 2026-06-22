export default function SessionsPagination({ currentPage, totalPages, onPreviousPage, onNextPage }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-gray-500">
      <span>当前页 {currentPage} / {totalPages}</span>
      <div className="flex items-center gap-2">
        <button
          className="btn-secondary px-3 py-1.5 text-xs"
          disabled={currentPage <= 1}
          onClick={onPreviousPage}
        >
          上一页
        </button>
        <span className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-600">
          {currentPage} / {totalPages}
        </span>
        <button
          className="btn-secondary px-3 py-1.5 text-xs"
          disabled={currentPage >= totalPages}
          onClick={onNextPage}
        >
          下一页
        </button>
      </div>
    </div>
  )
}
