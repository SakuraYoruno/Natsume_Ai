import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, RefreshCw, Clock3, Database, Activity, FileSearch, Radio } from 'lucide-react'
import { connectTraceRequestLive, fetchTraceNodes, fetchTraceStreamEvents } from '../api/obs.js'
import NodeDetailPanel from '../components/trace/NodeDetailPanel.jsx'
import StatusBadge from '../components/StatusBadge.jsx'

function JsonBlock({ data, emptyLabel = 'No data' }) {
  if (data == null || data === '') {
    return <div className="text-xs text-gray-400">{emptyLabel}</div>
  }

  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  return (
    <pre className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-700 whitespace-pre-wrap break-all overflow-auto max-h-64">
      {text}
    </pre>
  )
}

function MetricCard({ label, value, hint = null }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="text-xs font-medium text-gray-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-gray-900 break-all">{value ?? '-'}</div>
      {hint ? <div className="mt-1 text-xs text-gray-400">{hint}</div> : null}
    </div>
  )
}

function normalizeStatus(status) {
  const value = String(status || '').toUpperCase()
  if (['OK', 'UP', 'SUCCESS'].includes(value)) return 'healthy'
  if (['RUNNING', 'STARTED', 'ACCEPTED', 'DISPATCHED'].includes(value)) return 'running'
  if (['DEGRADED'].includes(value)) return 'degraded'
  if (['FAIL', 'FAILED', 'ERROR', 'DOWN'].includes(value)) return 'error'
  return 'unknown'
}

function formatDateTime(tsMs) {
  if (!tsMs) return '-'
  return new Date(tsMs).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function summarizeNode(node) {
  const output = node?.output_data
  const input = node?.input_data
  if (output && typeof output === 'object') {
    if (output.final_response) return String(output.final_response)
    if (output.detail) return String(output.detail)
    if (output.payload && typeof output.payload === 'object') {
      if (output.payload.pack_id) return `pack_id=${output.payload.pack_id}`
      if (output.payload.candidate_count != null) return `candidate_count=${output.payload.candidate_count}`
      if (output.payload.evidence_refs_count != null) return `evidence_refs_count=${output.payload.evidence_refs_count}`
    }
  }
  if (typeof output === 'string' && output.trim()) return output
  if (input && typeof input === 'object') {
    if (input.raw_text) return String(input.raw_text)
    if (input.routing_selected) return `route=${input.routing_selected}`
  }
  return node?.reason_code || node?.error || ''
}

function getNodePreview(node) {
  const summary = summarizeNode(node)
  return typeof summary === 'string' ? summary : ''
}

function buildSnapshotEntries(traceData) {
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  nodes.sort((a, b) => (b?.ts_ms ?? 0) - (a?.ts_ms ?? 0))
  return nodes.slice(0, 10).map((node, index) => ({
    entryId: `snapshot:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
    changeType: 'snapshot',
    label: 'Snapshot',
    nodeId: node.node_id,
    displayName: node.display_name || node.node_id,
    status: node.status,
    previousStatus: null,
    tsMs: node.ts_ms ?? null,
    preview: getNodePreview(node),
  }))
}

function buildDeltaEntries(deltaItem) {
  const changes = Array.isArray(deltaItem?.changes)
    ? [...deltaItem.changes].sort((a, b) => (b?.node?.ts_ms ?? 0) - (a?.node?.ts_ms ?? 0))
    : []

  return changes.map((change, index) => {
    const node = change?.node ?? {}
    const changeType = change?.change_type || 'updated'
    return {
      entryId: `${changeType}:${node.node_id}:${node.ts_ms ?? index}:${node.status ?? 'UNKNOWN'}`,
      changeType,
      label: changeType === 'added' ? 'Added' : 'Updated',
      nodeId: node.node_id,
      displayName: node.display_name || node.node_id,
      status: node.status,
      previousStatus: change?.previous_status || null,
      tsMs: node.ts_ms ?? null,
      preview: getNodePreview(node),
    }
  })
}

function mergeDeltaEntries(previous, incoming) {
  const merged = [...incoming, ...previous]
  const deduped = []
  const seen = new Set()
  for (const entry of merged) {
    if (!entry?.entryId || seen.has(entry.entryId)) continue
    seen.add(entry.entryId)
    deduped.push(entry)
    if (deduped.length >= 20) break
  }
  return deduped
}

function normalizeRecallItems(items) {
  if (!Array.isArray(items)) return []
  return items
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null
      const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {}
      const snippet = typeof item.snippet === 'string' && item.snippet.trim()
        ? item.snippet.trim()
        : (typeof item.text === 'string' ? item.text.trim() : '')
      return {
        key: `${item.doc_id || item.source_ref || 'item'}-${index}`,
        docId: item.doc_id || '-',
        sourceRef: item.source_ref || '-',
        evidenceRef: item.evidence_ref || '-',
        snippet: snippet || 'No snippet',
        score: item.score,
        confidence: item.confidence,
        storeId: metadata.store_id || '-',
        routeId: metadata.route_id || '-',
        collection: metadata.collection || '-',
      }
    })
    .filter(Boolean)
}

function normalizeRecallRefs(refs) {
  if (!Array.isArray(refs)) return []
  return refs
    .map((value) => (typeof value === 'string' ? value.trim() : ''))
    .filter(Boolean)
}

function buildRecallPreviewSections(recall) {
  if (!recall || typeof recall !== 'object') return []

  const sections = []
  const items = normalizeRecallItems(recall.items)
  const topk = normalizeRecallItems(recall.topk)
  const evidenceTopk = normalizeRecallItems(recall.evidence_topk)
  const selectedRefs = normalizeRecallRefs(recall.selected_refs || recall.evidence_refs)

  if (items.length > 0) {
    sections.push({ key: 'recall-items', title: 'Recall Candidates', type: 'items', items })
  }
  if (topk.length > 0) {
    sections.push({ key: 'rerank-topk', title: 'Rerank TopK', type: 'items', items: topk })
  }
  if (evidenceTopk.length > 0) {
    sections.push({ key: 'evidence-topk', title: 'Evidence Pack', type: 'items', items: evidenceTopk })
  }
  if (selectedRefs.length > 0) {
    sections.push({ key: 'selected-refs', title: 'Selected Evidence Refs', type: 'refs', refs: selectedRefs })
  }

  return sections
}

function getLiveBadgeClass(changeType) {
  if (changeType === 'added') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (changeType === 'updated') return 'border-blue-200 bg-blue-50 text-blue-700'
  return 'border-gray-200 bg-gray-50 text-gray-500'
}

function StageCard({ stage }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 space-y-3">
      <div className="flex items-center gap-2">
        <div className="text-sm font-semibold text-gray-900">{stage.op_kind}</div>
        <StatusBadge status={normalizeStatus(stage.status)} />
        {stage.reason_code ? (
          <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600">
            {stage.reason_code}
          </span>
        ) : null}
      </div>
      {stage.detail ? <div className="text-sm text-gray-600">{stage.detail}</div> : null}
      <JsonBlock data={stage.payload} emptyLabel="No payload" />
    </div>
  )
}

function RecallPreviewPanel({ recall }) {
  const sections = useMemo(() => buildRecallPreviewSections(recall), [recall])

  if (!recall) {
    return <div className="text-sm text-gray-400">No recall data for this request.</div>
  }

  return (
    <div className="space-y-4">
      {recall.message ? (
        <div className={`rounded-lg border px-3 py-2 text-xs ${recall.content_available ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
          {recall.message}
        </div>
      ) : null}

      {sections.length > 0 ? (
        <div className="space-y-3">
          {sections.map((section) => (
            <div key={section.key} className="space-y-2">
              <div className="text-xs font-medium text-gray-500">{section.title}</div>
              {section.type === 'items' ? (
                <div className="space-y-2">
                  {section.items.map((item) => (
                    <div key={item.key} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="font-medium text-gray-700">{item.docId}</span>
                        <span>{item.storeId}</span>
                        <span>{item.collection}</span>
                        <span>{item.routeId}</span>
                        {item.score != null ? <span>score={item.score}</span> : null}
                        {item.confidence != null ? <span>confidence={item.confidence}</span> : null}
                      </div>
                      <div className="mt-2 text-sm text-gray-700 break-all">{item.snippet}</div>
                      <div className="mt-2 text-xs text-gray-500 break-all">{item.sourceRef}</div>
                      {item.evidenceRef && item.evidenceRef !== '-' ? (
                        <div className="mt-1 text-xs text-gray-400 break-all">{item.evidenceRef}</div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="space-y-2">
                  {section.refs.map((value) => (
                    <div key={value} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600 break-all">
                      {value}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3">
        {(recall.stages || []).map((stage, index) => (
          <StageCard key={`${stage.op_kind}-${stage.ts_ms || index}`} stage={stage} />
        ))}
      </div>
    </div>
  )
}

function StreamEventsPanel({ events, loading, error }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
        <Activity size={15} className="text-sky-600" />
        <div className="text-sm font-semibold text-gray-900">Stream Events</div>
      </div>
      <div className="p-4">
        {loading ? <div className="text-sm text-gray-400">Loading stream events...</div> : null}
        {error ? <div className="text-sm text-red-500">{error}</div> : null}
        {!loading && !error && events.length === 0 ? (
          <div className="text-sm text-gray-400">No stream events for this request.</div>
        ) : null}
        {!loading && !error && events.length > 0 ? (
          <div className="space-y-2">
            {events.map((event, index) => (
              <div key={`${event.event_type}-${event.ts_ms}-${index}`} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <span>{event.phase || '-'}</span>
                  <span>{event.event_type}</span>
                  <span>{event.node_id || '-'}</span>
                  <span className="ml-auto">{formatDateTime(event.ts_ms)}</span>
                </div>
                {event.chunk ? <div className="mt-1 text-sm text-gray-700 break-all">{event.chunk}</div> : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function LiveNodeChangesPanel({ entries, connected, lastTs, error }) {
  const activeEntries = entries.length > 0 ? entries : []
  const statusClass = connected
    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
    : 'border-amber-200 bg-amber-50 text-amber-700'

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
        <Radio size={15} className="text-emerald-600" />
        <div className="text-sm font-semibold text-gray-900">Live Node Changes</div>
        <div className="ml-auto flex items-center gap-2 text-xs">
          <span className={`inline-flex items-center rounded-full border px-2.5 py-1 ${statusClass}`}>
            {connected ? 'Live stream connected' : 'Live stream reconnecting'}
          </span>
          {lastTs ? <span className="text-gray-400">{formatDateTime(lastTs)}</span> : null}
        </div>
      </div>
      <div className="p-4">
        {error ? <div className="mb-3 text-sm text-red-500">{error}</div> : null}
        {activeEntries.length === 0 ? (
          <div className="text-sm text-gray-400">Waiting for node changes.</div>
        ) : (
          <div className="space-y-2">
            {activeEntries.map((entry) => (
              <div key={entry.entryId} className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <div className="flex items-center gap-2">
                  <div className="text-sm font-medium text-gray-800">{entry.displayName || entry.nodeId}</div>
                  <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${getLiveBadgeClass(entry.changeType)}`}>
                    {entry.label}
                  </span>
                  <StatusBadge status={normalizeStatus(entry.status)} />
                  <div className="ml-auto text-xs text-gray-400">{formatDateTime(entry.tsMs)}</div>
                </div>
                <div className="mt-1 text-xs text-gray-500 break-all">{entry.nodeId}</div>
                {entry.previousStatus && entry.previousStatus !== entry.status ? (
                  <div className="mt-1 text-xs text-gray-500">
                    {entry.previousStatus} -&gt; {entry.status}
                  </div>
                ) : null}
                {entry.preview ? (
                  <div className="mt-1 text-xs text-gray-600 break-all">{entry.preview}</div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function TraceRequestDetail() {
  const navigate = useNavigate()
  const { requestId } = useParams()
  const [traceData, setTraceData] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [liveConnected, setLiveConnected] = useState(false)
  const [liveLastTs, setLiveLastTs] = useState(null)
  const [liveError, setLiveError] = useState(null)
  const [liveNodeEntries, setLiveNodeEntries] = useState([])

  const loadTrace = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const data = await fetchTraceNodes(requestId)
      setTraceData(data)
    } catch (err) {
      setError(err.message)
      setTraceData(null)
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [requestId])

  const loadEvents = useCallback(async ({ showLoading = false } = {}) => {
    if (!requestId) return
    if (showLoading) setEventsLoading(true)
    setEventsError(null)
    try {
      const data = await fetchTraceStreamEvents(requestId)
      setEvents(data.events ?? [])
    } catch (err) {
      setEvents([])
      setEventsError(err.message)
    } finally {
      if (showLoading) setEventsLoading(false)
    }
  }, [requestId])

  useEffect(() => {
    let cancelled = false

    async function loadInitial() {
      if (!requestId) return
      setLoading(true)
      setEventsLoading(true)
      try {
        const [traceResponse, eventsResponse] = await Promise.all([
          fetchTraceNodes(requestId),
          fetchTraceStreamEvents(requestId),
        ])
        if (cancelled) return
        setTraceData(traceResponse)
        setEvents(eventsResponse.events ?? [])
        setError(null)
        setEventsError(null)
      } catch (err) {
        if (cancelled) return
        setError(err.message)
      } finally {
        if (!cancelled) {
          setLoading(false)
          setEventsLoading(false)
        }
      }
    }

    loadInitial()
    return () => {
      cancelled = true
    }
  }, [requestId])

  useEffect(() => {
    if (!requestId) return undefined

    let closed = false
    let connection

    try {
      connection = connectTraceRequestLive(
        requestId,
        {
          onOpen: () => {
            if (closed) return
            setLiveConnected(true)
            setLiveError(null)
          },
          onSnapshot: (payload) => {
            if (closed) return
            const matchedItem = Array.isArray(payload?.items)
              ? payload.items.find((item) => item?.session?.request_id === requestId || item?.trace?.request_id === requestId)
              : null
            setLiveConnected(true)
            setLiveLastTs(payload?.timestamp ?? Date.now())
            if (matchedItem?.trace) {
              setTraceData(matchedItem.trace)
              setLoading(false)
              setError(null)
            }
            void loadEvents()
          },
          onDelta: (payload) => {
            if (closed) return
            const matchedItem = Array.isArray(payload?.items)
              ? payload.items.find((item) => item?.request_id === requestId || item?.session?.request_id === requestId)
              : null
            if (!matchedItem) return
            setLiveConnected(true)
            setLiveLastTs(payload?.timestamp ?? Date.now())
            setLiveNodeEntries((previous) => mergeDeltaEntries(previous, buildDeltaEntries(matchedItem)))
            void loadEvents()
          },
          onHeartbeat: (payload) => {
            if (closed) return
            setLiveConnected(true)
            setLiveLastTs(payload?.timestamp ?? Date.now())
          },
          onError: (payload) => {
            if (closed) return
            setLiveError(payload?.detail || 'trace live feed error')
          },
          onConnectionError: () => {
            if (closed) return
            setLiveConnected(false)
          },
        },
      )
    } catch (connectionError) {
      setLiveConnected(false)
      setLiveError(connectionError.message)
      return undefined
    }

    return () => {
      closed = true
      connection?.close?.()
    }
  }, [requestId, loadEvents])

  useEffect(() => {
    if (!requestId || liveConnected) return undefined
    const timer = window.setInterval(() => {
      void loadTrace()
      void loadEvents()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [requestId, liveConnected, loadTrace, loadEvents])

  useEffect(() => {
    const nodes = traceData?.nodes ?? []
    if (!nodes.length) {
      setSelectedNode(null)
      return
    }
    const preferred = nodes.find((node) => ['FAIL', 'FAILED', 'ERROR'].includes(String(node.status || '').toUpperCase()))
      || nodes[0]
    setSelectedNode((previous) => {
      if (previous?.node_id) {
        const matched = nodes.find((node) => node.node_id === previous.node_id)
        if (matched) return matched
      }
      return preferred
    })
  }, [traceData])

  const summary = traceData?.summary ?? {}
  const recall = summary.recall ?? null
  const tags = Array.isArray(summary.audit_tags) ? summary.audit_tags : []
  const nodes = Array.isArray(traceData?.nodes) ? [...traceData.nodes] : []
  nodes.sort((a, b) => {
    const aTs = a?.ts_ms ?? Number.MAX_SAFE_INTEGER
    const bTs = b?.ts_ms ?? Number.MAX_SAFE_INTEGER
    return aTs - bTs
  })

  const liveEntries = liveNodeEntries.length > 0 ? liveNodeEntries : buildSnapshotEntries(traceData)
  const changedNodeIds = new Set(liveEntries.slice(0, 8).map((entry) => entry.nodeId))

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <button
          className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          onClick={() => navigate('/trace')}
        >
          <ArrowLeft size={14} />
          Back
        </button>
        <div className="min-w-0">
          <div className="text-xs text-gray-500">Request Trace</div>
          <div className="font-mono text-sm text-gray-900 break-all">{requestId}</div>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {traceData ? <StatusBadge status={normalizeStatus(traceData.status)} /> : null}
          <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs ${liveConnected ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
            {liveConnected ? 'Live stream connected' : 'Live stream reconnecting'}
          </span>
          <button
            className="btn-secondary text-xs flex items-center gap-1"
            onClick={() => {
              void loadTrace({ showLoading: true })
              void loadEvents({ showLoading: true })
            }}
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-8 text-sm text-gray-400">Loading trace...</div>
      ) : null}
      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">{error}</div>
      ) : null}

      {!loading && !error && traceData ? (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <MetricCard label="Status" value={traceData.status} />
            <MetricCard label="Session" value={traceData.session_id || summary.session_id} />
            <MetricCard label="Total Latency" value={traceData.total_latency_ms != null ? `${traceData.total_latency_ms}ms` : '-'} />
            <MetricCard label="Node Count" value={traceData.node_count} hint={liveLastTs ? `live ${formatDateTime(liveLastTs)}` : null} />
          </div>

          <LiveNodeChangesPanel entries={liveEntries} connected={liveConnected} lastTs={liveLastTs} error={liveError} />

          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4 space-y-3">
              <div className="flex items-center gap-2">
                <FileSearch size={15} className="text-blue-600" />
                <div className="text-sm font-semibold text-gray-900">Request Summary</div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <div className="mb-1 text-xs font-medium text-gray-500">Input</div>
                  <JsonBlock data={summary.input_text || summary.intent} emptyLabel="No input captured" />
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium text-gray-500">Output</div>
                  <JsonBlock data={summary.output_text} emptyLabel="No output captured" />
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <MetricCard label="Task Type" value={summary.task_type} />
                <MetricCard label="Routing" value={summary.routing_selected} />
                <MetricCard label="Bond" value={summary.bond_stage} hint={summary.bond_score != null ? `score=${summary.bond_score}` : null} />
              </div>
              {tags.length > 0 ? (
                <div>
                  <div className="mb-2 text-xs font-medium text-gray-500">Audit Tags</div>
                  <div className="flex flex-wrap gap-2">
                    {tags.map((tag) => (
                      <span key={tag} className="rounded-full border border-gray-200 bg-gray-50 px-2 py-1 text-xs text-gray-600">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4 space-y-3">
              <div className="flex items-center gap-2">
                <Database size={15} className="text-cyan-600" />
                <div className="text-sm font-semibold text-gray-900">Recall Trace</div>
              </div>
              {recall ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <MetricCard label="Candidates" value={recall.candidate_count} />
                    <MetricCard label="TopK" value={recall.topk_count} />
                    <MetricCard label="Evidence Refs" value={recall.evidence_refs_count} />
                    <MetricCard label="Pack ID" value={recall.pack_id} />
                  </div>
                  <RecallPreviewPanel recall={recall} />
                </>
              ) : (
                <div className="text-sm text-gray-400">No recall data for this request.</div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_20rem] gap-4 items-start">
            <div className="space-y-4">
              <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
                <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3">
                  <Clock3 size={15} className="text-violet-600" />
                  <div className="text-sm font-semibold text-gray-900">Node Timeline</div>
                </div>
                <div className="divide-y divide-gray-100">
                  {nodes.map((node, index) => {
                    const selected = selectedNode?.node_id === node.node_id
                    const preview = summarizeNode(node)
                    const changed = changedNodeIds.has(node.node_id)
                    return (
                      <button
                        key={`${node.node_id}-${index}`}
                        className={`w-full text-left px-4 py-4 transition-colors ${selected ? 'bg-blue-50' : changed ? 'bg-emerald-50/50 hover:bg-emerald-50' : 'bg-white hover:bg-gray-50'}`}
                        onClick={() => setSelectedNode(node)}
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-gray-400">{String(index + 1).padStart(2, '0')}</span>
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-gray-900 truncate">
                              {node.display_name || node.node_id}
                            </div>
                            <div className="text-xs text-gray-500">
                              {node.node_id} / {node.node_type}{node.side ? ` / ${node.side}` : ''}
                            </div>
                          </div>
                          <div className="ml-auto flex items-center gap-2">
                            {changed ? (
                              <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                                live
                              </span>
                            ) : null}
                            {node.latency_ms != null ? (
                              <span className="text-xs text-gray-500">{node.latency_ms}ms</span>
                            ) : null}
                            <StatusBadge status={normalizeStatus(node.status)} />
                          </div>
                        </div>
                        {preview ? (
                          <div className="mt-2 text-sm text-gray-600 break-all">{preview}</div>
                        ) : null}
                        {node.reason_code ? (
                          <div className="mt-2 inline-flex rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600">
                            {node.reason_code}
                          </div>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </div>

              <StreamEventsPanel events={events} loading={eventsLoading} error={eventsError} />
            </div>

            <div className="xl:sticky xl:top-4">
              {selectedNode ? (
                <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
              ) : (
                <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-400">
                  Select a node to inspect its input and output payload.
                </div>
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
