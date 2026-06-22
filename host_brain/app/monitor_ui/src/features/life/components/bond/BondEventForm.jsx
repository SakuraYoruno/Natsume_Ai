import { useEffect, useState } from 'react'

const UPDATE_KIND_OPTIONS = [
  { value: 'INTERACTION', label: '日常互动' },
  { value: 'DEEP_TALK', label: '深度交流' },
  { value: 'EMOTIONAL_SUPPORT', label: '情绪支持' },
  { value: 'SHARED_EXPERIENCE', label: '共同经历' },
  { value: 'CONFLICT', label: '冲突摩擦' },
  { value: 'RECONCILIATION', label: '和解修复' },
]

const EMOTION_TAG_OPTIONS = [
  { value: 'NEUTRAL', label: '平静' },
  { value: 'JOY', label: '喜悦' },
  { value: 'SADNESS', label: '难过' },
  { value: 'ANGER', label: '愤怒' },
  { value: 'FEAR', label: '害怕' },
  { value: 'SURPRISE', label: '惊讶' },
  { value: 'DISGUST', label: '排斥' },
]

export default function BondEventForm({ onSubmit, loading, selectedUser }) {
  const [form, setForm] = useState({
    user_id: selectedUser || '',
    update_kind: 'INTERACTION',
    evidence_refs: '',
    emotion_tag: 'NEUTRAL',
    emotion_intensity: '0.5',
  })

  useEffect(() => {
    if (!selectedUser) return
    setForm((current) => ({ ...current, user_id: selectedUser }))
  }, [selectedUser])

  const handleSubmit = (event) => {
    event.preventDefault()
    const refs = form.evidence_refs
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    if (!form.user_id || refs.length === 0) return

    onSubmit({
      ...form,
      evidence_refs: refs,
      emotion_intensity: Number(form.emotion_intensity),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-gray-500">用户 ID</label>
          <input
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.user_id}
            onChange={(event) => setForm((current) => ({ ...current, user_id: event.target.value }))}
            placeholder="例如 wxid_user_001"
            required
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">事件类型</label>
          <select
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.update_kind}
            onChange={(event) => setForm((current) => ({ ...current, update_kind: event.target.value }))}
          >
            {UPDATE_KIND_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-gray-500">证据引用（逗号分隔）</label>
        <input
          className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
          value={form.evidence_refs}
          onChange={(event) => setForm((current) => ({ ...current, evidence_refs: event.target.value }))}
          placeholder="例如 trace_001, message_002"
          required
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-gray-500">情绪标签</label>
          <select
            className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-300"
            value={form.emotion_tag}
            onChange={(event) => setForm((current) => ({ ...current, emotion_tag: event.target.value }))}
          >
            {EMOTION_TAG_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">
            情绪强度（{form.emotion_intensity}）
          </label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            className="mt-1 w-full"
            value={form.emotion_intensity}
            onChange={(event) => setForm((current) => ({ ...current, emotion_intensity: event.target.value }))}
          />
        </div>
      </div>

      <button
        type="submit"
        disabled={loading}
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? '提交中…' : '提交关系事件'}
      </button>
    </form>
  )
}
