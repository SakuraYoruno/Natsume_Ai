import { useCallback, useMemo, useState } from 'react'
import { RefreshCw, Workflow } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
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
  focusExecutionReview,
  normalizeExecutionIndex,
  normalizeReviewLocator,
  parseTaskReviewDeepLink,
  selectLatestExecutionForGraph,
} from '../features/execution/taskRuntimeModel.js'
import {
  useTaskReviewLocatorQuery,
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
  const [searchParams] = useSearchParams()
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [selectedKey, setSelectedKey] = useState('')
  const [afterCursor, setAfterCursor] = useState(0)
  const [actorId, setActorId] = useState('monitor-operator')
  const [resumeTokens, setResumeTokens] = useState({})
  const [busyReviewId, setBusyReviewId] = useState('')
  const [actionNotice, setActionNotice] = useState(null)

  const deepLink = useMemo(
    () => parseTaskReviewDeepLink(searchParams.toString()),
    [searchParams],
  )
  const locatorQuery = useTaskReviewLocatorQuery({
    reviewId: deepLink.reviewId,
    enabled: deepLink.active && !deepLink.error,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  })
  const locatorModel = useMemo(() => (
    locatorQuery.data !== null
      ? normalizeReviewLocator(locatorQuery.data, deepLink.reviewId)
      : null
  ), [deepLink.reviewId, locatorQuery.data])
  const needsIndex = !deepLink.active || Boolean(
    locatorModel?.compatible && locatorModel.found && locatorModel.taskGraphId,
  )
  const indexQuery = useTaskRuntimeIndexQuery({
    enabled: needsIndex,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  })
  const indexModel = useMemo(() => normalizeExecutionIndex(indexQuery.data), [indexQuery.data])
  const locatorExecution = useMemo(() => (
    locatorModel?.taskGraphId
      ? selectLatestExecutionForGraph(indexModel.executions, locatorModel.taskGraphId)
      : null
  ), [indexModel.executions, locatorModel])

  const defaultSelectedKey = indexModel.executions.length
    ? `${indexModel.executions[0].graph_id}@${indexModel.executions[0].revision}`
    : ''
  const effectiveSelectedKey = deepLink.active
    ? (locatorExecution ? `${locatorExecution.graph_id}@${locatorExecution.revision}` : '')
    : (selectedKey || defaultSelectedKey)
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
  const executionReviewFocus = useMemo(() => (
    model.compatible && deepLink.reviewId
      ? focusExecutionReview(model.reviews, deepLink.reviewId)
      : { found: false, review: null, reviews: [], error: null }
  ), [deepLink.reviewId, model])
  const standaloneReview = Boolean(
    deepLink.active
    && locatorModel?.compatible
    && locatorModel.found
    && !locatorModel.taskGraphId
    && locatorModel.review,
  )
  const reviewFocusReady = standaloneReview || Boolean(
    locatorModel?.taskGraphId && executionReviewFocus.found,
  )

  let deepLinkError = deepLink.error
  if (deepLink.active && !deepLinkError && locatorQuery.error) {
    deepLinkError = `Review locator 请求失败：${locatorQuery.error.message}`
  }
  if (deepLink.active && !deepLinkError && locatorModel?.error) {
    deepLinkError = locatorModel.error
  }
  if (
    deepLink.active
    && !deepLinkError
    && locatorQuery.status === 'success'
    && !locatorModel
  ) {
    deepLinkError = 'Review locator 未返回版本化载荷，命令入口保持关闭。'
  }
  if (deepLink.active && !deepLinkError && locatorModel?.taskGraphId && indexQuery.error) {
    deepLinkError = `Execution index 请求失败：${indexQuery.error.message}`
  }
  if (
    deepLink.active
    && !deepLinkError
    && locatorModel?.taskGraphId
    && indexQuery.status === 'success'
    && !indexModel.compatible
  ) {
    deepLinkError = indexModel.error
  }
  if (
    deepLink.active
    && !deepLinkError
    && locatorModel?.taskGraphId
    && indexModel.compatible
    && indexQuery.status === 'success'
    && !locatorExecution
  ) {
    deepLinkError = `TaskGraph ${locatorModel.taskGraphId} 不在 execution index 中，命令入口保持关闭。`
  }
  if (deepLink.active && !deepLinkError && locatorExecution && executionQuery.error) {
    deepLinkError = `Execution read model 请求失败：${executionQuery.error.message}`
  }
  if (
    deepLink.active
    && !deepLinkError
    && locatorExecution
    && executionQuery.status === 'success'
    && !model.compatible
  ) {
    deepLinkError = model.error
  }
  if (
    deepLink.active
    && !deepLinkError
    && locatorExecution
    && model.compatible
    && executionQuery.status === 'success'
    && !executionReviewFocus.found
  ) {
    deepLinkError = executionReviewFocus.error
  }

  const deepLinkPending = Boolean(deepLink.active && !deepLinkError && !reviewFocusReady)

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
      const refreshes = []
      if (deepLink.active && deepLink.reviewId) refreshes.push(locatorQuery.refetch())
      if (selected.graphId && selected.revision) refreshes.push(executionQuery.refetch())
      await Promise.all(refreshes)
    } catch (error) {
      setActionNotice({ variant: 'error', text: error?.message || 'Review 命令失败' })
    } finally {
      setBusyReviewId('')
    }
  }, [deepLink.active, deepLink.reviewId, executionQuery, locatorQuery, selected.graphId, selected.revision])

  const setResumeToken = useCallback((reviewId, value) => {
    setResumeTokens((current) => ({ ...current, [reviewId]: value }))
  }, [])

  const refreshCurrent = useCallback(() => {
    const refreshes = []
    if (deepLink.active && deepLink.reviewId) refreshes.push(locatorQuery.refetch())
    if (needsIndex) refreshes.push(indexQuery.refetch())
    if (selected.graphId && selected.revision) refreshes.push(executionQuery.refetch())
    return Promise.all(refreshes)
  }, [deepLink.active, deepLink.reviewId, executionQuery, indexQuery, locatorQuery, needsIndex, selected.graphId, selected.revision])

  const combinedError = deepLink.active
    ? (locatorQuery.error || (locatorModel?.taskGraphId ? indexQuery.error || executionQuery.error : null))
    : (indexQuery.error || executionQuery.error)
  const loading = deepLink.active
    ? deepLinkPending
    : indexQuery.loading || executionQuery.loading
  const refreshing = locatorQuery.refreshing || (needsIndex && indexQuery.refreshing) || executionQuery.refreshing
  const execution = model.execution
  const reviewPanelReviews = deepLink.active
    ? (standaloneReview ? [locatorModel.review] : executionReviewFocus.reviews)
    : model.reviews
  const displayMetrics = standaloneReview
    ? { nodes: 0, attempts: 0, reviews: 1, events: 0 }
    : model.metrics
  const displayGeneratedAtMs = standaloneReview ? locatorModel.generatedAtMs : model.generatedAtMs
  const readModelReady = deepLink.active ? reviewFocusReady : model.compatible
  const displayError = deepLink.active ? deepLinkError : combinedError
  const heroTitle = standaloneReview
    ? `Review ${deepLink.reviewId} · ${locatorModel.review.status}`
    : execution
      ? `${execution.graph_id}@${execution.revision} · ${execution.status}`
      : deepLink.active
        ? `定位 Review ${deepLink.reviewId || 'invalid'}`
        : '等待版本化执行快照'

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
          onClick={refreshCurrent}
        >
          <RefreshCw size={14} className={loading || refreshing ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      <PageHero
        icon={Workflow}
        eyebrow="Canonical execution read model"
        title={heroTitle}
        description="UI 只消费版本化 execution / review locator 公开白名单；Review 写入复用受治理命令，Resume 始终要求绑定 token。"
        gradientClass="from-indigo-50 via-white to-cyan-50"
        metaItems={[
          `Nodes ${displayMetrics.nodes}`,
          `Attempts ${displayMetrics.attempts}`,
          `Reviews ${displayMetrics.reviews}`,
          `Cursor ${model.cursorPage.next_cursor || 0}`,
        ]}
        aside={(
          <SampleMeta
            className="max-w-md"
            loadState={displayError ? 'error' : loading ? 'loading' : readModelReady ? 'ready' : 'unavailable'}
            sampleSize={standaloneReview ? 1 : displayMetrics.events}
            sampleLabel={standaloneReview ? 'public review' : 'cursor events'}
            updatedAt={displayGeneratedAtMs}
            source={standaloneReview ? 'ReviewStore locator' : 'TaskStore + ReviewStore + RuntimeEventStore'}
            definition="所有展示字段来自显式版本化白名单；raw thought、内部表和 token secret 不进入 read model。"
          />
        )}
      />

      {!deepLink.active && combinedError ? (
        <NoticeBanner variant="warning" title="Operator view 不可用">
          {combinedError.message}. 如处于回滚态，请确认 HOST_NLE_OPERATOR_VIEWS_ENABLED。
        </NoticeBanner>
      ) : null}
      {deepLinkPending ? (
        <NoticeBanner variant="info" title="正在定位真实 Review">
          先验证单条只读 locator；有 TaskGraph 绑定时再选择 execution index 中的最新 revision。
        </NoticeBanner>
      ) : null}
      {deepLink.active && deepLinkError ? (
        <NoticeBanner variant="error" title="Review 深链已拒绝">
          {deepLinkError} 未定位前不会显示或开放其他 Review 命令。
        </NoticeBanner>
      ) : null}
      {deepLink.active && reviewFocusReady ? (
        <NoticeBanner variant="success" title="Review 深链已定位">
          {locatorModel.taskGraphId
            ? `${deepLink.reviewId} 已在 ${locatorExecution.graph_id}@${locatorExecution.revision} 的真实 execution Review 中 exact-match。`
            : `${deepLink.reviewId} 是未绑定 TaskGraph 的 durable Review；当前展示来自 locator 公开白名单。`}
        </NoticeBanner>
      ) : null}
      {!deepLink.active && indexQuery.data && !indexModel.compatible ? <NoticeBanner variant="error" title="Execution index 已拒绝">{indexModel.error}</NoticeBanner> : null}
      {!deepLink.active && executionQuery.data && !model.compatible ? <NoticeBanner variant="error" title="Execution read model 已拒绝">{model.error}</NoticeBanner> : null}
      {actionNotice ? <NoticeBanner variant={actionNotice.variant} title="Review 命令结果">{actionNotice.text}</NoticeBanner> : null}

      {!deepLink.active && indexModel.executions.length ? (
        <TaskGraphPicker executions={indexModel.executions} selectedKey={effectiveSelectedKey} onSelect={handleSelect} />
      ) : null}

      {model.compatible || standaloneReview ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <MetricCard label="Graph" value={standaloneReview ? 'UNBOUND' : execution?.status || '—'} hint={standaloneReview ? 'durable review' : execution?.terminal ? 'terminal' : 'in progress'} tone="text-indigo-600" />
            <MetricCard label="DAG nodes" value={displayMetrics.nodes} hint={`${model.edges.length} dependencies`} tone="text-blue-600" />
            <MetricCard label="Attempts" value={displayMetrics.attempts} hint={`${model.budgets.length} budgets`} tone="text-sky-600" />
            <MetricCard label="Reviews" value={displayMetrics.reviews} hint={`${displayMetrics.events} replay events`} tone="text-violet-600" />
          </div>

          {model.compatible ? <TaskDagPanel nodes={model.nodes} edges={model.edges} /> : null}

          {model.compatible ? (
            <div className="grid gap-6 xl:grid-cols-2">
              <TaskBudgetPanel budgets={model.budgets} />
              <TaskVerificationPanel verification={model.verification} />
            </div>
          ) : null}

          {!deepLink.active || reviewFocusReady ? (
            <TaskReviewPanel
              reviews={reviewPanelReviews}
              focusedReviewId={deepLink.active ? deepLink.reviewId : ''}
              restrictCommandsToFocused={deepLink.active}
              actorId={actorId}
              setActorId={setActorId}
              resumeTokens={resumeTokens}
              setResumeToken={setResumeToken}
              busyReviewId={busyReviewId}
              onApprove={(review) => runReviewAction(review, () => approveTaskReview(review, actorId))}
              onReject={(review) => runReviewAction(review, () => rejectTaskReview(review, actorId))}
              onResume={(review) => runReviewAction(review, () => resumeTaskReview(review, resumeTokens[review.review_id] || ''))}
            />
          ) : null}

          {model.compatible ? (
            <TaskCursorTimeline
              timeline={model.timeline}
              cursorPage={model.cursorPage}
              onReplayStart={() => setAfterCursor(0)}
              onReplayNext={() => setAfterCursor(model.cursorPage.next_cursor || 0)}
            />
          ) : null}
        </>
      ) : null}
    </div>
  )
}
