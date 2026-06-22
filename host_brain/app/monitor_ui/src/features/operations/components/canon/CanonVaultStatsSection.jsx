import { Lock } from 'lucide-react'

import EmptyStateCard from '../../../../shared/ui/EmptyStateCard.jsx'
import MetricCard from '../../../../shared/ui/MetricCard.jsx'
import NoticeBanner from '../../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../../shared/ui/observability/SampleMeta.jsx'
import SectionHeading from '../../../../shared/ui/SectionHeading.jsx'

export default function CanonVaultStatsSection({
  recentManifestRows,
  vaultData,
  vaultError,
  vaultLoadState,
  vaultMetricCards,
  vaultProgress,
  vaultUpdatedAt,
}) {
  if (!vaultData) return null

  return (
    <div className="card border border-gray-200 p-4">
      <SectionHeading icon={Lock} title="变更清单统计" tone="text-violet-600" />
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {vaultMetricCards.map((card) => (
          <MetricCard key={card.label} label={card.label} value={card.value} variant="stacked" tone={card.tone} />
        ))}
      </div>

      <div className="mb-3">
        <SampleMeta
          loadState={vaultLoadState}
          sampleSize={vaultData.total_manifests ?? 0}
          sampleLabel="清单"
          updatedAt={vaultUpdatedAt}
          source="vault manifest"
          definition="变更清单统计独立于提案/文档加载；如果这里只失败，不会再误伤当前标签页的主数据状态。"
        />
      </div>

      {vaultError ? (
        <NoticeBanner variant="warning" title="变更清单统计暂不可用">
          {vaultError}
        </NoticeBanner>
      ) : null}

      {vaultProgress ? (
        <div className="mb-3">
          <div className="mb-1 flex justify-between text-xs text-gray-400">
            {vaultProgress.map((item) => (
              <span key={item.label}>{item.label} {item.pctLabel}</span>
            ))}
          </div>
          <div className="flex h-2 overflow-hidden rounded">
            {vaultProgress.map((item) => (
              <div key={item.label} className={item.className} style={{ width: item.width }} />
            ))}
          </div>
        </div>
      ) : null}

      {recentManifestRows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="border-b border-gray-100 bg-gray-50">
              <tr>
                {['清单 ID', '类型', '状态', '可逆', '摘要'].map((title) => (
                  <th key={title} className="px-3 py-1.5 text-left font-medium text-gray-500">
                    {title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {recentManifestRows.map((item) => (
                <tr key={item.key} className="hover:bg-gray-50">
                  <td className="max-w-[120px] truncate px-3 py-1.5 font-mono text-violet-600" title={item.manifestTitle}>
                    {item.manifestId}
                  </td>
                  <td className="px-3 py-1.5 text-gray-500">{item.artifactType}</td>
                  <td className="px-3 py-1.5">
                    <span
                      className={`rounded border px-1.5 py-0.5 font-medium ${item.statusClassName}`}
                    >
                      {item.statusLabel}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-center">
                    {item.reversible ? <span className="text-green-600">✓</span> : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="max-w-[160px] truncate px-3 py-1.5 text-gray-400" title={item.summaryTitle}>
                    {item.summary}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyStateCard title="暂无变更清单记录" hint="等待设定变更写入后展示。" className="p-6" />
      )}
    </div>
  )
}
