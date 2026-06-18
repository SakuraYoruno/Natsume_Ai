import React from 'react'
import { Database, FileText, Wand2 } from 'lucide-react'
import EmptyStateCard from '../EmptyStateCard.jsx'
import NoticeBanner from '../NoticeBanner.jsx'

function formatTemplateContent(content) {
  if (content === null || content === undefined || content === '') return '—'
  if (typeof content === 'string') return content
  if (typeof content === 'number' || typeof content === 'boolean') return String(content)
  try {
    return JSON.stringify(content, null, 2)
  } catch {
    return String(content)
  }
}

function ContentCard({ icon: Icon, title, content, mono = false }) {
  const displayContent = formatTemplateContent(content)

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-gray-700">
        <Icon size={14} /> {title}
      </div>
      <pre
        className={`max-h-[320px] overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-600 ${
          mono ? 'font-mono' : ''
        }`}
      >
        {displayContent}
      </pre>
    </div>
  )
}

export default function PromptTemplateEditorPanel({
  selected,
  emptyTitle,
  emptyHint,
  headerEyebrow,
  title,
  description,
  badges = null,
  inspectorTop = null,
  detailLoading,
  detailRefreshing,
  detailError,
  detailUpdatedAt,
  detailStale,
  defaultTitle = 'YAML 默认内容',
  resolvedTitle = '当前生效内容',
  overrideTitle = '当前运行时覆盖',
  detailMeta = null,
}) {
  if (!selected) {
    return (
      <EmptyStateCard
        title={emptyTitle}
        hint={emptyHint}
        className="min-h-[420px] flex flex-col items-center justify-center"
      />
    )
  }

  const mono = selected.content_type === 'json'
  const hasOverride = Boolean(selected.has_override)

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          {headerEyebrow ? <div className="mb-1 text-xs text-gray-400">{headerEyebrow}</div> : null}
          <h2 className="text-base font-semibold text-gray-900 break-all">{title}</h2>
          {description ? <p className="mt-1 text-sm text-gray-500 leading-6">{description}</p> : null}
        </div>
        {badges ? <div className="flex items-center gap-2 flex-wrap">{badges}</div> : null}
      </div>

      <NoticeBanner variant="info" title="YAML 为唯一真源">
        前端当前只读展示模板目录、YAML 默认值、当前生效值和现存 runtime override，不支持直接保存或恢复模板。
      </NoticeBanner>

      {hasOverride ? (
        <NoticeBanner variant="warning" title="当前存在运行时覆盖">
          当前模板仍有 runtime override，实际生效内容可能与 YAML 默认值不同。这里仅做差异展示，不提供前端修改入口。
        </NoticeBanner>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr,320px]">
        <div className="space-y-3">
          {inspectorTop}
          <ContentCard icon={FileText} title={defaultTitle} content={selected.default_content} mono={mono} />
          {hasOverride ? (
            <ContentCard icon={Database} title={overrideTitle} content={selected.override_content} mono={mono} />
          ) : null}
        </div>

        <div className="space-y-3">
          <ContentCard icon={Wand2} title={resolvedTitle} content={selected.resolved_content} mono={mono} />
          {detailMeta}
        </div>
      </div>

      {detailError ? (
        <NoticeBanner variant="warning" title="模板详情暂不可用">
          {detailError}
          {detailUpdatedAt ? ` · 最近更新时间 ${new Date(detailUpdatedAt).toLocaleString('zh-CN')}` : ''}
        </NoticeBanner>
      ) : null}

      {detailLoading || detailRefreshing || detailStale ? (
        <NoticeBanner variant="info" title="模板刷新中">
          {detailLoading || detailRefreshing ? '正在同步模板详情…' : '模板详情已超过建议新鲜度阈值，可手动刷新。'}
        </NoticeBanner>
      ) : null}
    </div>
  )
}
