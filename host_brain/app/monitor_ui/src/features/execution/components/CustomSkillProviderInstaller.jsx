import { useEffect, useState } from 'react'
import { installCustomSkillProvider } from '../api.js'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'
import SampleMeta from '../../../shared/ui/observability/SampleMeta.jsx'
import {
  useCustomSkillProviderTemplateQuery,
  useCustomSkillProvidersQuery,
} from '../toolsQueries.js'
import {
  CUSTOM_SKILL_PROVIDER_DEFAULT_FORM,
  buildCustomSkillProviderInstallRequest,
  getCustomSkillProviderTemplateLoadState,
  getCustomSkillProvidersLoadState,
  mergeCustomSkillProviderTemplate,
} from '../toolsOperationsModel.js'
import CustomSkillProviderForm from './CustomSkillProviderForm.jsx'
import CustomSkillProviderList from './CustomSkillProviderList.jsx'

export default function CustomSkillProviderInstaller() {
  const [installing, setInstalling] = useState(false)
  const [result, setResult] = useState(null)
  const [form, setForm] = useState(CUSTOM_SKILL_PROVIDER_DEFAULT_FORM)
  const {
    providersData,
    providers,
    providersError,
    loading: providersLoading,
    refreshing: providersRefreshing,
    stale: providersStale,
    lastUpdatedAt: providersUpdatedAt,
    refetch: refetchProviders,
  } = useCustomSkillProvidersQuery()
  const {
    templateData,
    templateError,
    loading: templateLoading,
    refreshing: templateRefreshing,
    stale: templateStale,
    lastUpdatedAt: templateUpdatedAt,
    refetch: refetchTemplate,
  } = useCustomSkillProviderTemplateQuery()

  useEffect(() => {
    if (!templateData) return
    setForm((prev) => mergeCustomSkillProviderTemplate(prev, templateData))
  }, [templateData])

  const providersLoadState = getCustomSkillProvidersLoadState({
    providersData,
    providers,
    providersError,
    providersLoading,
    providersRefreshing,
    providersStale,
  })
  const templateLoadState = getCustomSkillProviderTemplateLoadState({
    templateData,
    templateError,
    templateLoading,
    templateRefreshing,
    templateStale,
  })

  const handleLoadTemplate = async () => {
    const nextTemplate = await refetchTemplate()
    if (!nextTemplate) return
    setForm((prev) => mergeCustomSkillProviderTemplate(prev, nextTemplate))
  }

  const handleInstall = async () => {
    setInstalling(true)
    setResult(null)
    try {
      const data = await installCustomSkillProvider(buildCustomSkillProviderInstallRequest(form))
      setResult({ ok: true, data })
      await refetchProviders()
    } catch (installError) {
      setResult({ ok: false, error: installError.message || '安装失败' })
    } finally {
      setInstalling(false)
    }
  }

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-gray-700">自定义技能提供器</div>
          <div className="mt-1 text-xs text-gray-500">
            直接写入 <code className="font-mono">host_brain/skills/custom_providers</code>，并可选自动刷新技能注册表。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleLoadTemplate()}
            disabled={templateLoading || templateRefreshing}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {templateLoading || templateRefreshing ? '同步中…' : '加载模板'}
          </button>
          <button
            onClick={() => void refetchProviders()}
            disabled={providersLoading || providersRefreshing}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            {providersLoading || providersRefreshing ? '刷新中…' : '刷新列表'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <div className="space-y-4">
          <SampleMeta
            loadState={templateLoadState}
            sampleSize={templateData?.source_code ? 1 : 0}
            sampleLabel="模板"
            updatedAt={templateUpdatedAt}
            source="custom provider template"
            definition="模板区用于生成新的 custom provider 脚手架；这里的草稿是本地状态，不会被列表刷新覆盖。"
          />

          <CustomSkillProviderForm
            form={form}
            installing={installing}
            result={result}
            templateError={templateError}
            onChange={setForm}
            onInstall={handleInstall}
          />
        </div>

        <div className="space-y-4">
          <SampleMeta
            loadState={providersLoadState}
            sampleSize={providers.length}
            sampleLabel="提供器"
            updatedAt={providersUpdatedAt}
            source="custom providers"
            definition="提供器列表已独立进入 query 层；刷新列表只会刷新 provider 清单，不会影响模板编辑区的本地草稿。"
          />

          {providersError ? (
            <NoticeBanner variant={providers.length > 0 ? 'warning' : 'error'}>
              {providersError}
            </NoticeBanner>
          ) : null}

          <CustomSkillProviderList
            providers={providers}
            providersLoading={providersLoading}
          />
        </div>
      </div>
    </div>
  )
}
