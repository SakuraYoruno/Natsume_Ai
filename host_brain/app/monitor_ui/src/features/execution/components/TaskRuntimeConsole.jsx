import {
  CheckCircle2,
  CircleDashed,
  Clock3,
  GitBranch,
  KeyRound,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'
import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import { formatTime } from '../../../shared/formatters.js'

const STATUS_CLASS = {
  COMPLETED: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  ACCEPTED: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  PASS: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  APPROVED: 'border-blue-200 bg-blue-50 text-blue-700',
  ACTIVE: 'border-blue-200 bg-blue-50 text-blue-700',
  RUNNING: 'border-blue-200 bg-blue-50 text-blue-700',
  PENDING: 'border-amber-200 bg-amber-50 text-amber-700',
  WAITING_APPROVAL: 'border-amber-200 bg-amber-50 text-amber-700',
  EXPIRED: 'border-gray-200 bg-gray-100 text-gray-600',
  REJECTED: 'border-rose-200 bg-rose-50 text-rose-700',
  FAILED: 'border-rose-200 bg-rose-50 text-rose-700',
  FAIL: 'border-rose-200 bg-rose-50 text-rose-700',
  VERIFICATION_FAILED: 'border-rose-200 bg-rose-50 text-rose-700',
  CANCELLED: 'border-gray-200 bg-gray-100 text-gray-600',
  CONSUMED: 'border-violet-200 bg-violet-50 text-violet-700',
  NOT_RUN: 'border-gray-200 bg-white text-gray-500',
}

function StatusBadge({ status }) {
  const value = String(status || 'UNKNOWN').toUpperCase()
  return (
    <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${STATUS_CLASS[value] || 'border-gray-200 bg-gray-50 text-gray-600'}`}>
      {value}
    </span>
  )
}

function amountLabel(amount = {}) {
  return `${Number(amount.token_count || 0)} tokens · ${Number(amount.tool_call_count || 0)} tools · ${Number(amount.time_ms || 0)}ms`
}

export function TaskGraphPicker({ executions = [], selectedKey, onSelect }) {
  return (
    <section className="card p-4" aria-label="TaskGraph selector">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-900">TaskGraph 选择</div>
          <div className="mt-1 text-xs text-gray-500">只消费版本化 execution index，不读取内部表。</div>
        </div>
        <label className="text-xs font-medium text-gray-600">
          选择 TaskGraph
          <select
            aria-label="选择 TaskGraph"
            className="ml-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800"
            value={selectedKey}
            onChange={(event) => onSelect(event.target.value)}
          >
            {executions.map((item) => {
              const key = `${item.graph_id}@${item.revision}`
              return <option key={key} value={key}>{key} · {item.status}</option>
            })}
          </select>
        </label>
      </div>
    </section>
  )
}

export function TaskDagPanel({ nodes = [], edges = [] }) {
  return (
    <section className="card p-4" aria-label="TaskGraph DAG">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
          <GitBranch size={16} className="text-indigo-600" />
          TaskGraph DAG
        </div>
        <span className="text-xs text-gray-400">{nodes.length} nodes · {edges.length} edges</span>
      </div>
      {nodes.length ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {nodes.map((node) => (
            <article key={node.node_id} className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-gray-900">{node.node_id}</div>
                  <div className="mt-1 text-xs text-gray-500">route · {node.route || '—'}</div>
                </div>
                <StatusBadge status={node.status} />
              </div>
              <div className="mt-3 text-xs text-gray-500">
                依赖：{node.dependencies?.length ? node.dependencies.join(', ') : 'root'} · 风险 L{node.risk_level || 1}
              </div>
              <div className="mt-3 rounded-xl bg-gray-50 px-3 py-2 text-xs text-gray-600">
                Verification <span className="font-semibold">{node.verification?.status || 'NOT_RUN'}</span>
                {' · '}{node.verification?.evidence_refs?.length || 0} evidence refs
              </div>
              <div className="mt-3 space-y-2">
                {(node.attempts || []).map((attempt) => (
                  <div key={attempt.attempt_id} className="flex items-center justify-between gap-3 rounded-xl border border-gray-100 px-3 py-2 text-xs">
                    <span className="font-medium text-gray-700">Attempt #{attempt.attempt_no} · {attempt.attempt_id}</span>
                    <StatusBadge status={attempt.status} />
                  </div>
                ))}
                {!node.attempts?.length ? <div className="text-xs text-gray-400">尚无 attempt</div> : null}
              </div>
            </article>
          ))}
        </div>
      ) : <EmptyStateCard title="暂无 TaskGraph 节点" hint="等待 canonical runtime 产出版本化节点。" className="p-6" />}
    </section>
  )
}

export function TaskBudgetPanel({ budgets = [] }) {
  return (
    <section className="card p-4" aria-label="Task budget reservations">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-900">
        <Clock3 size={16} className="text-sky-600" />
        Budget reservations
      </div>
      {budgets.length ? (
        <div className="space-y-3">
          {budgets.map((budget) => (
            <article key={budget.budget_id} className="rounded-2xl border border-gray-200 bg-gray-50 p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-gray-800">{budget.budget_id}</div>
                  <div className="mt-1 text-xs text-gray-500">{budget.scope} · {budget.node_id || 'graph scope'}</div>
                </div>
                <StatusBadge status={budget.status} />
              </div>
              <div className="mt-3 grid gap-2 text-xs text-gray-600 sm:grid-cols-2">
                <div className="rounded-lg bg-white px-3 py-2">Consumed · {amountLabel(budget.consumed)}</div>
                <div className="rounded-lg bg-white px-3 py-2">Limit · {amountLabel(budget.limits)}</div>
              </div>
              {budget.exceeded_dimensions?.length ? (
                <div className="mt-2 text-xs font-medium text-rose-600">Exceeded: {budget.exceeded_dimensions.join(', ')}</div>
              ) : null}
            </article>
          ))}
        </div>
      ) : <EmptyStateCard title="暂无预算预留" hint="当前执行没有 budget reservation 样本。" className="p-6" />}
    </section>
  )
}

export function TaskVerificationPanel({ verification = [] }) {
  return (
    <section className="card p-4" aria-label="Task verification status">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-900">
        <ShieldCheck size={16} className="text-emerald-600" />
        Verification
      </div>
      <div className="space-y-2">
        {verification.map((item) => (
          <div key={item.node_id} className="flex items-center justify-between gap-3 rounded-xl border border-gray-200 px-3 py-3">
            <div>
              <div className="text-sm font-medium text-gray-800">{item.node_id}</div>
              <div className="mt-1 text-xs text-gray-500">{item.source} · {item.evidence_refs?.length || 0} evidence refs</div>
            </div>
            <StatusBadge status={item.status} />
          </div>
        ))}
        {!verification.length ? <div className="text-sm text-gray-400">暂无 verification 状态。</div> : null}
      </div>
    </section>
  )
}

export function TaskReviewPanel({
  reviews = [],
  actorId,
  setActorId,
  resumeTokens,
  setResumeToken,
  busyReviewId,
  onApprove,
  onReject,
  onResume,
}) {
  return (
    <section className="card p-4" aria-label="Review command rail">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
            <KeyRound size={16} className="text-violet-600" />
            Review command rail
          </div>
          <div className="mt-1 text-xs text-gray-500">批准不会自动恢复；恢复必须提交一次性、版本绑定的 token。</div>
        </div>
        <label className="text-xs font-medium text-gray-600">
          Reviewer
          <input
            aria-label="Reviewer"
            className="ml-2 rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={actorId}
            onChange={(event) => setActorId(event.target.value)}
          />
        </label>
      </div>
      {reviews.length ? (
        <div className="space-y-3">
          {reviews.map((review) => {
            const action = review.actionState || {}
            const busy = busyReviewId === review.review_id
            const token = resumeTokens[review.review_id] || ''
            return (
              <article key={review.review_id} className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-gray-900">{review.review_id}</div>
                    <div className="mt-1 text-xs text-gray-500">{review.review_type} · {review.resume_phase}</div>
                  </div>
                  <StatusBadge status={review.status} />
                </div>
                <div className="mt-3 text-sm text-gray-700">{review.reason}</div>
                <div className="mt-2 text-xs text-gray-500">
                  subject v{review.subject_version} · checkpoint {review.checkpoint_id} · expires {formatTime(review.expires_at)}
                </div>

                {action.expired ? (
                  <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-xs font-medium text-gray-600">
                    已过期，approve / reject / resume 操作入口关闭。
                  </div>
                ) : null}

                {action.canApprove || action.canReject ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      className="btn-primary"
                      disabled={busy || !actorId.trim()}
                      onClick={() => onApprove(review)}
                    >
                      <CheckCircle2 size={14} /> 批准 {review.review_id}
                    </button>
                    <button
                      className="btn-secondary text-rose-600"
                      disabled={busy || !actorId.trim()}
                      onClick={() => onReject(review)}
                    >
                      <XCircle size={14} /> 拒绝 {review.review_id}
                    </button>
                  </div>
                ) : null}

                {action.canResume ? (
                  <div className="mt-4 rounded-2xl border border-violet-100 bg-violet-50/60 p-3">
                    <label className="block text-xs font-medium text-violet-800">
                      Resume token {review.review_id}
                      <input
                        type="password"
                        aria-label={`Resume token ${review.review_id}`}
                        autoComplete="off"
                        className="mt-2 w-full rounded-lg border border-violet-200 bg-white px-3 py-2 text-sm text-gray-800"
                        value={token}
                        onChange={(event) => setResumeToken(review.review_id, event.target.value)}
                        placeholder="粘贴 approve 返回的一次性 token"
                      />
                    </label>
                    <button
                      className="btn-primary mt-3"
                      disabled={busy || !token.trim()}
                      onClick={() => onResume(review)}
                    >
                      <RotateCcw size={14} /> 恢复 {review.review_id}
                    </button>
                  </div>
                ) : null}
              </article>
            )
          })}
        </div>
      ) : <EmptyStateCard title="暂无 Review" hint="当前 TaskGraph 没有 review item。" className="p-6" />}
    </section>
  )
}

export function TaskCursorTimeline({ timeline = [], cursorPage = {}, onReplayStart, onReplayNext }) {
  return (
    <section className="card p-4" aria-label="Runtime cursor timeline">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
            <CircleDashed size={16} className="text-cyan-600" />
            Cursor timeline
          </div>
          <div className="mt-1 text-xs text-gray-500">
            after {cursorPage.after || 0} → next {cursorPage.next_cursor || 0} · dedupe_key 去重
          </div>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={onReplayStart}>从头回放</button>
          <button className="btn-secondary" disabled={!cursorPage.has_more} onClick={onReplayNext}>继续游标</button>
        </div>
      </div>
      {timeline.length ? (
        <ol className="space-y-3">
          {timeline.map((event) => (
            <li key={`${event.cursor}-${event.event_id}`} className="relative rounded-2xl border border-gray-200 bg-gray-50 p-3 pl-14">
              <span className="absolute left-3 top-3 inline-flex h-8 min-w-8 items-center justify-center rounded-full bg-cyan-100 px-2 text-xs font-semibold text-cyan-800">
                {event.cursor}
              </span>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-gray-800">{event.event_type}</span>
                <span className="text-xs text-gray-400">{formatTime(event.ts_ms)}</span>
              </div>
              <div className="mt-1 text-xs text-gray-600">{event.summary}</div>
              <div className="mt-2 text-[11px] text-gray-400">{event.payload_ref} · {event.evidence_refs?.length || 0} evidence refs</div>
            </li>
          ))}
        </ol>
      ) : <EmptyStateCard title="当前游标页无事件" hint="从头回放或等待 Runtime Event 发布。" className="p-6" />}
    </section>
  )
}
