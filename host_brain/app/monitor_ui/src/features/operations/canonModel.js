import { resolveLoadState } from '../../models/loadState.js'
import {
  CANON_MANIFEST_STATUS_CLASSES,
  CANON_MANIFEST_STATUS_LABELS,
  resolveStatusClass,
  resolveStatusLabel,
} from '../../shared/statusMeta.js'

export const CANON_PAGE_SIZE = 20

export const CANON_TABS = [
  { key: 'proposals', label: '提案列表' },
  { key: 'documents', label: '主设定文档' },
]

export function getCanonTabState(activeTab) {
  return {
    proposalsActive: activeTab === 'proposals',
    documentsActive: activeTab === 'documents',
  }
}

export function buildCanonActiveLoadState({
  proposalsActive = true,
  proposalsData = null,
  documentsData = null,
  proposals = [],
  documents = [],
  total = 0,
  proposalsLoading = false,
  proposalsRefreshing = false,
  documentsLoading = false,
  documentsRefreshing = false,
  proposalsError = null,
  documentsError = null,
  proposalsStale = false,
  documentsStale = false,
} = {}) {
  const activeError = proposalsActive ? proposalsError : documentsError
  const sampleSize = proposalsActive ? total : documents.length
  const activeData = proposalsActive ? proposalsData : documentsData

  return {
    activeError,
    sampleSize,
    loadState: resolveLoadState({
      loading: proposalsActive
        ? proposalsLoading || proposalsRefreshing
        : documentsLoading || documentsRefreshing,
      error: activeError,
      hasData: Boolean(activeData),
      hasSamples: proposalsActive ? total > 0 : documents.length > 0,
      available: Boolean(activeData),
      stale: proposalsActive ? proposalsStale : documentsStale,
    }),
  }
}

export function buildCanonVaultLoadState({
  vaultData = null,
  vaultError = null,
  vaultLoading = false,
  vaultRefreshing = false,
  vaultStale = false,
} = {}) {
  return resolveLoadState({
    loading: vaultLoading || vaultRefreshing,
    error: vaultError,
    hasData: Boolean(vaultData),
    hasSamples: Number(vaultData?.total_manifests ?? 0) > 0,
    available: Boolean(vaultData),
    stale: vaultStale,
  })
}

export function buildCanonHeroView({
  proposalsActive = true,
  documents = [],
  total = 0,
  statusFilter = '',
  vaultData = null,
  proposalsUpdatedAt = null,
  documentsUpdatedAt = null,
  activeLoadState = null,
} = {}) {
  return {
    title: proposalsActive ? '提案审核' : '文档浏览',
    metaItems: [
      proposalsActive ? `提案数：${total}` : `文档数：${documents.length}`,
      `清单：${vaultData?.total_manifests ?? 0}`,
      proposalsActive
        ? `状态过滤：${statusFilter || '全部'}`
        : '当前标签：主设定文档',
    ],
    sampleMeta: {
      loadState: activeLoadState,
      sampleSize: proposalsActive ? total : documents.length,
      sampleLabel: proposalsActive ? '提案' : '文档',
      updatedAt: proposalsActive ? proposalsUpdatedAt : documentsUpdatedAt,
      source: proposalsActive ? 'canon proposals' : 'canon documents',
      definition: proposalsActive
        ? '提案审核与主设定文档已拆成独立模块；未打开的标签页不会再提前装配重型表格与卡片。'
        : '主设定文档现在按标签页独立加载；切回提案页时才会装配审核表格与操作逻辑。',
    },
  }
}

export function isCanonRefreshing({
  proposalsLoading = false,
  proposalsRefreshing = false,
  documentsLoading = false,
  documentsRefreshing = false,
  vaultLoading = false,
  vaultRefreshing = false,
} = {}) {
  return Boolean(
    proposalsLoading
    || proposalsRefreshing
    || documentsLoading
    || documentsRefreshing
    || vaultLoading
    || vaultRefreshing,
  )
}

export function buildCanonVaultMetricCards(vaultData = {}) {
  return [
    { label: '总清单', value: vaultData?.total_manifests ?? 0 },
    { label: '生效中', value: vaultData?.active_count ?? 0, tone: 'text-green-600' },
    { label: '已撤销', value: vaultData?.undone_count ?? 0, tone: 'text-orange-500' },
    { label: '已回收', value: vaultData?.gc_count ?? 0, tone: 'text-gray-500' },
  ]
}

function buildCanonVaultProgressItem(label, count, total, className) {
  const pct = total > 0 ? (Number(count || 0) / total) * 100 : 0
  return {
    label,
    pctLabel: `${Math.round(pct)}%`,
    width: `${pct}%`,
    className,
  }
}

export function buildCanonVaultProgress(vaultData = {}) {
  const total = Number(vaultData?.total_manifests ?? 0)
  if (total <= 0) return null

  return [
    buildCanonVaultProgressItem('生效中', vaultData?.active_count, total, 'bg-green-500'),
    buildCanonVaultProgressItem('已撤销', vaultData?.undone_count, total, 'bg-orange-400'),
    buildCanonVaultProgressItem('已回收', vaultData?.gc_count, total, 'bg-gray-300'),
  ]
}

export function buildCanonRecentManifestRows(vaultData = {}) {
  const rows = Array.isArray(vaultData?.recent_manifests) ? vaultData.recent_manifests : []
  return rows.map((item, index) => ({
    key: `${item?.manifest_id || 'manifest'}-${index}`,
    manifestId: item?.manifest_id || '—',
    manifestTitle: item?.manifest_id,
    artifactType: item?.artifact_type || '—',
    statusLabel: resolveStatusLabel(CANON_MANIFEST_STATUS_LABELS, item?.status),
    statusClassName: resolveStatusClass(
      CANON_MANIFEST_STATUS_CLASSES,
      item?.status,
      'border-gray-200 bg-gray-100 text-gray-500',
    ),
    reversible: Boolean(item?.is_reversible),
    summary: item?.summary || '—',
    summaryTitle: item?.summary,
  }))
}

export function buildCanonViewModel({
  activeTab = 'proposals',
  statusFilter = '',
  proposalsData = null,
  proposals = [],
  total = 0,
  proposalsError = null,
  proposalsLoading = false,
  proposalsRefreshing = false,
  proposalsStale = false,
  proposalsUpdatedAt = null,
  documentsData = null,
  documents = [],
  documentsError = null,
  documentsLoading = false,
  documentsRefreshing = false,
  documentsStale = false,
  documentsUpdatedAt = null,
  vaultData = null,
  vaultError = null,
  vaultLoading = false,
  vaultRefreshing = false,
  vaultStale = false,
} = {}) {
  const { proposalsActive, documentsActive } = getCanonTabState(activeTab)
  const { activeError, loadState: activeLoadState } = buildCanonActiveLoadState({
    proposalsActive,
    proposalsData,
    documentsData,
    proposals,
    documents,
    total,
    proposalsLoading,
    proposalsRefreshing,
    documentsLoading,
    documentsRefreshing,
    proposalsError,
    documentsError,
    proposalsStale,
    documentsStale,
  })

  return {
    proposalsActive,
    documentsActive,
    activeError,
    activeLoadState,
    vaultLoadState: buildCanonVaultLoadState({
      vaultData,
      vaultError,
      vaultLoading,
      vaultRefreshing,
      vaultStale,
    }),
    heroView: buildCanonHeroView({
      proposalsActive,
      documents,
      total,
      statusFilter,
      vaultData,
      proposalsUpdatedAt,
      documentsUpdatedAt,
      activeLoadState,
    }),
    refreshing: isCanonRefreshing({
      proposalsLoading,
      proposalsRefreshing,
      documentsLoading,
      documentsRefreshing,
      vaultLoading,
      vaultRefreshing,
    }),
    vaultMetricCards: buildCanonVaultMetricCards(vaultData),
    vaultProgress: buildCanonVaultProgress(vaultData),
    recentManifestRows: buildCanonRecentManifestRows(vaultData),
  }
}
