import { useMemo, useState } from 'react'
import {
  Beaker,
  Container,
  FileCheck2,
  FlaskConical,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import EvolutionLabConsole from '../features/evolutionLab/components/EvolutionLabConsole.jsx'
import { buildEvolutionLabViewModel } from '../features/evolutionLab/evolutionLabModel.js'
import { useEvolutionLabSuitesQuery } from '../features/evolutionLab/evolutionLabQueries.js'
import MetricCard from '../shared/ui/MetricCard.jsx'
import NoticeBanner from '../shared/ui/NoticeBanner.jsx'
import PageHero from '../shared/ui/PageHero.jsx'
import useAppStore from '../store/useAppStore.js'

export default function EvolutionLab() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [workspaceDraft, setWorkspaceDraft] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [cursorStack, setCursorStack] = useState([''])
  const [selectedSuiteId, setSelectedSuiteId] = useState('')
  const cursor = cursorStack[cursorStack.length - 1]
  const query = useEvolutionLabSuitesQuery({
    limit: 20,
    cursor,
    workspaceId,
    pollingEnabled,
    pollingInterval,
    pollingTick,
  })
  const model = useMemo(() => buildEvolutionLabViewModel(query.data), [query.data])

  const effectiveSelectedSuiteId = model.suites.some(
    (suite) => suite.suiteId === selectedSuiteId,
  )
    ? selectedSuiteId
    : (model.suites[0]?.suiteId || '')

  function applyWorkspaceFilter(event) {
    event.preventDefault()
    setWorkspaceId(workspaceDraft.trim())
    setCursorStack([''])
    setSelectedSuiteId('')
  }

  return (
    <div className="space-y-4">
      <PageHero
        icon={FlaskConical}
        eyebrow="NE-04 · Trusted Evolution Lab"
        title="演化实验室"
        description="只读查看隔离 Docker 双臂、隐藏验证器、反事实差异与待 Review 提案。这里没有生产激活入口。"
        gradientClass="from-indigo-50 via-white to-emerald-50"
        metaItems={['read-only', 'proposal-only', 'zero production effects']}
      />

      {!model.safe ? (
        <NoticeBanner variant="error" title="可信证据投影已 fail-closed">
          {model.error || query.error || 'Suite 证据不可用。'}
        </NoticeBanner>
      ) : (
        <NoticeBanner variant="info" title="Lab 不是生产 authority">
          Golden Report 只可生成待 Review 的 EvolutionProposal；没有真实 ReviewItem 时不会伪造跳转，也不会修改策略、Skill、Knowledge 或 Identity。
        </NoticeBanner>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={Beaker} label="Trusted suites" value={model.metrics.suites} hint="commit-bound aggregate" />
        <MetricCard icon={FileCheck2} label="Reports" value={model.metrics.reports} hint="Research / Proactive / Memory" />
        <MetricCard icon={Container} label="Docker arms" value={model.metrics.runs} hint="independent baseline + candidate" />
        <MetricCard icon={ShieldCheck} label="Pending proposals" value={model.metrics.proposals} hint="Review required" />
      </div>

      <section className="card p-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <form className="flex flex-1 flex-wrap items-end gap-2" onSubmit={applyWorkspaceFilter}>
            <label className="min-w-[260px] flex-1 text-xs font-medium text-gray-600">
              Workspace filter
              <input
                className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800"
                value={workspaceDraft}
                onChange={(event) => setWorkspaceDraft(event.target.value)}
                placeholder="留空查看全部 workspace"
              />
            </label>
            <button type="submit" className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-700">
              应用
            </button>
          </form>
          <button
            type="button"
            onClick={() => query.refetch()}
            disabled={query.loading}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 disabled:opacity-50"
          >
            <RefreshCw size={14} className={query.loading ? 'animate-spin' : ''} />
            刷新投影
          </button>
        </div>
      </section>

      {query.error && model.safe ? (
        <NoticeBanner variant="error" title="Obs 查询失败">{query.error}</NoticeBanner>
      ) : null}
      {model.safe ? (
        <EvolutionLabConsole
          suites={model.suites}
          selectedSuiteId={effectiveSelectedSuiteId}
          onSelectSuite={setSelectedSuiteId}
        />
      ) : null}

      {model.safe ? (
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            disabled={cursorStack.length <= 1}
            onClick={() => setCursorStack((current) => current.slice(0, -1))}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 disabled:opacity-40"
          >
            上一页
          </button>
          <span className="text-xs text-gray-400">
            第 {cursorStack.length} 页{model.sourceTruncated ? ' · canonical source 已有更多历史' : ''}
          </span>
          <button
            type="button"
            disabled={!model.hasMore || !model.nextCursor}
            onClick={() => setCursorStack((current) => [...current, model.nextCursor])}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-semibold text-gray-700 disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      ) : null}
    </div>
  )
}
