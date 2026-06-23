import JsonBlock from '../../../shared/ui/JsonBlock.jsx'
import NoticeBanner from '../../../shared/ui/NoticeBanner.jsx'

export default function CustomSkillProviderForm({
  form,
  installing,
  result,
  templateError,
  onChange,
  onInstall,
}) {
  return (
    <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div>
        <label className="mb-1 block text-xs text-gray-500">文件名</label>
        <input
          className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.filename}
          onChange={(event) => onChange((prev) => ({ ...prev, filename: event.target.value }))}
          placeholder="my_skill_provider.py"
        />
        <div className="mt-1 text-xs text-gray-400">
          文件名会规范为 <code className="font-mono">*_provider.py</code>。
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-gray-500">源码</label>
        <textarea
          className="h-80 w-full resize-y rounded border border-gray-300 px-3 py-2 font-mono text-xs leading-5 focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.source_code}
          onChange={(event) => onChange((prev) => ({ ...prev, source_code: event.target.value }))}
          placeholder="# 在这里粘贴 provider 源码"
        />
      </div>

      <label className="flex items-center gap-2 text-sm text-gray-600">
        <input
          type="checkbox"
          checked={form.activate_now}
          onChange={(event) => onChange((prev) => ({ ...prev, activate_now: event.target.checked }))}
        />
        安装后立即激活
      </label>

      <button
        onClick={() => void onInstall()}
        disabled={installing}
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {installing ? '安装中…' : '安装技能提供器'}
      </button>

      {result ? (
        result.ok ? (
          <NoticeBanner variant="success" title="安装完成">
            <JsonBlock
              data={result.data}
              variant="light"
              className="mt-1 border-green-200 bg-white/70 font-mono text-green-900"
            />
          </NoticeBanner>
        ) : (
          <NoticeBanner variant="error" title="安装失败">
            {result.error}
          </NoticeBanner>
        )
      ) : null}

      {templateError ? (
        <NoticeBanner variant="warning" title="模板暂不可用">
          {templateError}
        </NoticeBanner>
      ) : null}
    </div>
  )
}
