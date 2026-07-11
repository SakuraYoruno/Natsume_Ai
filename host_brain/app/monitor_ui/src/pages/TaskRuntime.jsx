import { useCallback, useMemo, useState } from 'react'
import { RefreshCw, Workflow } from 'lucide-react'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import SampleMeta from '../shared/ui/observability/SampleMeta.jsx'
import {
  TaskBudgetPanel,
  TaskCursorTimeline,
  TaskDagPanel,
  TaskGraphPicker,
  TaskReviewPanel,
  TaskVerificationPanel,
} from '../features/execution/components/TaskRuntimeConsole.jsx'
import {
  approveTaskReview,
  rejectTaskReview,
  resumeTaskReview,
} from '../features/execution/taskRuntimeApi.js'
import {
  buildTaskRuntimeViewModel,
  normalizeExecutionIndex,
} from '../features/execution/taskRuntimeModel.js'
import {
  useTaskRuntimeExecutionQuery,
  useTaskRuntimeIndexQuery,
} from '../features/execution/taskRuntimeQueries.js'
import useAppStore from '../store/useAppStore.js'

function splitExecutionKey(value) {
  const text = String(value || '')
  const separator = text.lastIndexOf('@')
  if (separator <= 0) return { graphId: '', revision: 0 }
  return {
    graphId: text.slice(0, separator),
    revision: Number(text.slice(separator + 1)) || 0,
  }
}

export default function TaskRuntime() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [selectedKey, setSelectedKey] = useState('')
  const [afterCursor, setAfterCursor] = useState(0)
  const [actorId, setActorId] = useState('monitor-operator')
  const [resumeTokens, setResumeTokens] = useState({})
  const [busyReviewId, setBusyReviewId] = useState('')
  const [actionNotice, setActionNotice] = useState(null)

  const indexQuery = useTaskRuntimeIndexQuery({ pollingEnabled, pollingInterval, pollingTick })
  const indexModel = useMemo(() => normalizeExecutionIndex(indexQuery.data), [indexQuery.data])

  const defaultSelectedKey = indexModel.executions.length
    ? `${indexModel.executions[0].graph_id}@${indexModel.executions[0].revision}`
    : ''
  const effectiveSelectedKey = selectedKey || defaultSelectedKey
  const selected = useMemo(() => splitExecutionKey(effectiveSelectedKey), [effectiveSelectedKey])
  const executionQuery = useTaskRuntimeExecutionQuery({
    graphId: selected.graphId,
    revision: selected.revision,
    afterCursor,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  })
  const model = useMemo(() => buildTaskRuntimeViewModel(executionQuery.data), [executionQuery.data])

  const handleSelect = useCallback((value) => {
    setSelectedKey(value)
    setAfterCursor(0)
    setActionNotice(null)
  }, [])

  const runReviewAction = useCallback(async (review, action) => {
    setBusyReviewId(review.review_id)
    setActionNotice(null)
    try {
      const response = await action()
      if (response?.resume_token_ref) {
        setResumeTokens((current) => ({ ...current, [review.review_id]: response.resume_token_ref }))
      }
      setActionNotice({ variant: 'success', text: `${review.review_id} 命令已受理；页面正在刷新 durable 状态。` })
      await executionQuery.refetch()
    } catch (error) {
      setActionNotice({ variant: 'error', text: error?.message || 'Review 命令失败' })
    } finally {
      setBusyReviewId('')
    }
  }, [executionQuery])

  const setResumeToken = useCallback((reviewId, value) => {
    setResumeTokens((current) => ({ ...current, [reviewId]: value }))
  }, [])

  const combinedError = indexQuery.error || executionQuery.error
  const loading = indexQuery.loading || executionQuery.loading
  const refreshing = indexQuery.refreshing || executionQuery.refreshing
  const execution = model.execution

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900">
            <Workflow size={20} className="text-indigo-600" />
            执行审查
          </h1>
          <p className="text-sm text-gray-500">回放 TaskGraph、attempt、budget、verification、review 与 durable cursor。</p>
        </div>
        <button
          className="btn-secondary"
          disabled={loading || refreshing}
          onClick={() => Promise.all([indexQuery.refetch(), executionQuery.refetch()])}
        >
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Workflow}
        eyebrow="Canonical execution read model"
        title={execution ? `${execution.graph_id}@${execution.revision} · ${execution.status}` : '等待版本化执行快照'}
        description="UI 只消费 Natsume.operator.execution_review.v1；Review 写入复用受治理命令，Resume 始终要求绑定 token。"
        gradientClass="from-indigo-50 via-white to-cyan-50"
        metaItems={[
          `Nodes ${model.metrics.nodes}`,
          `Attempts ${model.metrics.attempts}`,
          `Reviews ${model.metrics.reviews}`,
          `Cursor ${model.cursorPage.next_cursor || 0}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={combinedError ? 'error' : loading ? 'loading' : model.compatible ? 'ready' : 'unavailable'}
            sampleSize={model.metrics.events}
            sampleLabel="cursor events"
            updatedAt={model.generatedAtMs}
            source="TaskStore + ReviewStore + RuntimeEventStore"
            definition="所有展示字段来自显式版本化白名单；raw thought、内部表和 token secret 不进入 read model。"
          />
        )}
      />

      {combinedError ? (
        <NoticeBanner variant="warning" title="Operator view 不可用">
          {combinedError.message}. 如处于回滚态，请确认 HOST_NLE_OPERATOR_VIEWS_ENABLED。
        </NoticeBanner>
      ) : null}
      {indexQuery.data && !indexModel.compatible ? <NoticeBanner variant="error" title="Execution index 已拒绝">{indexModel.error}</NoticeBanner> : null}
      {executionQuery.data && !model.compatible ? <NoticeBanner variant="error" title="Execution read model 已拒绝">{model.error}</NoticeBanner> : null}
      {actionNotice ? <NoticeBanner variant={actionNotice.variant} title="Review 命令结果">{actionNotice.text}</NoticeBanner> : null}

      {indexModel.executions.length ? (
        <TaskGraphPicker executions={indexModel.executions} selectedKey={effectiveSelectedKey} onSelect={handleSelect} />
      ) : null}

      {model.compatible ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="Graph" value={execution?.status || '—'} hint={execution?.terminal ? 'terminal' : 'in progress'} tone="text-indigo-600" />
            <MetricCard label="DAG nodes" value={model.metrics.nodes} hint={`${model.edges.length} dependencies`} tone="text-blue-600" />
            <MetricCard label="Attempts" value={model.metrics.attempts} hint={`${model.budgets.length} budgets`} tone="text-sky-600" />
            <MetricCard label="Reviews" value={model.metrics.reviews} hint={`${model.metrics.events} replay events`} tone="text-violet-600" />
          </div>

          <TaskDagPanel nodes={model.nodes} edges={model.edges} />

          <div className="grid gap-6 xl:grid-cols-2">
            <TaskBudgetPanel budgets={model.budgets} />
            <TaskVerificationPanel verification={model.verification} />
          </div>

          <TaskReviewPanel
            reviews={model.reviews}
            actorId={actorId}
            setActorId={setActorId}
            resumeTokens={resumeTokens}
            setResumeToken={setResumeToken}
            busyReviewId={busyReviewId}
            onApprove={(review) => runReviewAction(review, () => approveTaskReview(review, actorId))}
            onReject={(review) => runReviewAction(review, () => rejectTaskReview(review, actorId))}
            onResume={(review) => runReviewAction(review, () => resumeTaskReview(review, resumeTokens[review.review_id] || ''))}
          />

          <TaskCursorTimeline
            timeline={model.timeline}
            cursorPage={model.cursorPage}
            onReplayStart={() => setAfterCursor(0)}
            onReplayNext={() => setAfterCursor(model.cursorPage.next_cursor || 0)}
          />
        </>
      ) : null}
    </div>
  )
}
