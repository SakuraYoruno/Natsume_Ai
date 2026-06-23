import EmptyStateCard from '../../../shared/ui/EmptyStateCard.jsx'
import LoadingNotice from '../../../shared/ui/LoadingNotice.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import { formatDateTime } from '../../../shared/formatters.js'
import {
  TOOL_PROVIDER_STATUS_CLASSES,
  TOOL_PROVIDER_STATUS_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../../shared/statusMeta.js'

function ProviderStatusBadge({ status }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        resolveStatusClass(TOOL_PROVIDER_STATUS_CLASSES, status, 'bg-gray-100 text-gray-600')
      }`}
    >
      {resolveStatusLabel(TOOL_PROVIDER_STATUS_LABELS, status, '未知')}
    </span>
  )
}

function SkillNameBadges({
  title,
  names,
  provider,
  toneClassName,
  keyPrefix,
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold text-gray-500">{title}</div>
      <div className="flex flex-wrap gap-1.5">
        {names.map((name) => (
          <span key={`${provider.filename}-${keyPrefix}-${name}`} className={`rounded-full px-2 py-0.5 text-xs ${toneClassName}`}>
            {name}
          </span>
        ))}
      </div>
    </div>
  )
}

function ProviderLoadingState() {
  return (
    <LoadingNotice>
      正在同步自定义提供器列表…
    </LoadingNotice>
  )
}

export default function CustomSkillProviderList({
  providers,
  providersLoading,
}) {
  if (providersLoading && providers.length === 0) {
    return <ProviderLoadingState />
  }

  if (providers.length === 0) {
    return <EmptyStateCard title="暂无自定义提供器" hint="安装后会在这里出现。" className="p-6" />
  }

  return (
    <div className="space-y-3">
      {providers.map((provider) => (
        <div key={provider.filename} className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium text-gray-800">{provider.filename}</div>
              <div className="mt-0.5 font-mono text-xs text-gray-500">{provider.module_name}</div>
            </div>
            <ProviderStatusBadge status={provider.status} />
          </div>

          <div className="space-y-1 text-xs text-gray-500">
            <div>
              路径：<code className="font-mono">{provider.path}</code>
            </div>
            <div>
              最近更新时间：
              {formatDateTime(provider.updated_at_ms)}
            </div>
          </div>

          {(provider.declared_skill_names || []).length > 0 ? (
            <SkillNameBadges
              title="声明技能"
              names={provider.declared_skill_names}
              provider={provider}
              toneClassName="bg-sky-50 text-sky-700"
              keyPrefix="declared"
            />
          ) : null}

          {(provider.active_skill_names || []).length > 0 ? (
            <SkillNameBadges
              title="当前启用技能"
              names={provider.active_skill_names}
              provider={provider}
              toneClassName="bg-emerald-50 text-emerald-700"
              keyPrefix="active"
            />
          ) : null}

          {provider.error ? <NoticeBanner variant="error">{provider.error}</NoticeBanner> : null}
        </div>
      ))}
    </div>
  )
}
