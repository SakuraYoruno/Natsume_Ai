export default function ImmuneGateToolbar({
  windowMinutes,
  onWindowMinutesChange,
  replayRequestId,
  onReplayRequestIdChange,
  loading,
  refreshing,
  replayLoading,
  replayRefreshing,
  onRefresh,
  onReplayLoad,
}) {
  const busy = loading || refreshing || replayLoading || replayRefreshing
  const replayBusy = replayLoading || replayRefreshing
  const replayDisabled = replayLoading || !String(replayRequestId || '').trim()

  return (
    <div className="flex items-center justify-between gap-2 flex-wrap">
      <div>
        <div className="text-sm font-semibold text-gray-700">护栏与回放</div>
        <div className="text-xs text-gray-500">运行态、事件统计与原因排行。</div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <select
          className="rounded-lg border border-primary-100/70 bg-white/80 px-2.5 py-1.5 text-xs text-gray-600"
          value={windowMinutes}
          onChange={(event) => onWindowMinutesChange(Number(event.target.value))}
        >
          <option value={60}>60m</option>
          <option value={360}>6h</option>
          <option value={1440}>24h</option>
          <option value={10080}>7d</option>
        </select>
        <button
          onClick={onRefresh}
          disabled={busy}
          className="btn-secondary text-xs disabled:opacity-50"
        >
          {busy ? '刷新中…' : '刷新'}
        </button>
        <input
          value={replayRequestId}
          onChange={(event) => onReplayRequestIdChange(event.target.value)}
          placeholder="request_id"
          className="w-44 rounded-lg border border-primary-100/70 bg-white/80 px-2.5 py-1.5 text-xs text-gray-600"
        />
        <button
          onClick={() => onReplayLoad(replayRequestId)}
          disabled={replayDisabled}
          className="btn-secondary border-violet-200 text-xs text-violet-700 disabled:opacity-40"
        >
          {replayBusy ? '回放中…' : '回放'}
        </button>
        <a
          href="#/operations/prompt?keyword=immune_agent.runtime_rules&scope=immune_agent"
          className="btn-secondary border-blue-200 text-xs text-blue-700"
        >
          编辑规则
        </a>
      </div>
    </div>
  )
}
