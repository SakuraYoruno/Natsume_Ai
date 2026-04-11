import React, { useEffect, useState, useCallback } from 'react'
import {
  RefreshCw, Package, CheckCircle, XCircle, AlertTriangle,
  Shield, Search, FolderOpen, Globe, Zap, Tag,
} from 'lucide-react'
import { fetchSkillsHubStats } from '../api/obs.js'
import EmptyStateCard from '../components/EmptyStateCard.jsx'
import NoticeBanner from '../components/NoticeBanner.jsx'
import PageHero from '../components/PageHero.jsx'
import useAppStore from '../store/useAppStore.js'

const CLEARANCE_STYLES = {
  LOW:    { bg: 'bg-green-100',  text: 'text-green-700',  label: 'LOW' },
  MEDIUM: { bg: 'bg-yellow-100', text: 'text-yellow-700', label: 'MEDIUM' },
  HIGH:   { bg: 'bg-red-100',    text: 'text-red-700',    label: 'HIGH' },
}

const SOURCE_STYLES = {
  builtin:  { bg: 'bg-blue-100',   text: 'text-blue-700',   label: '内置', icon: Package },
  local:    { bg: 'bg-green-100',  text: 'text-green-700',  label: '本地', icon: FolderOpen },
  github:   { bg: 'bg-purple-100', text: 'text-purple-700', label: 'GitHub', icon: Globe },
  external: { bg: 'bg-orange-100', text: 'text-orange-700', label: '外部', icon: FolderOpen },
  hub:      { bg: 'bg-indigo-100', text: 'text-indigo-700', label: 'Hub', icon: Zap },
}

function SkillCard({ skill }) {
  const cl = CLEARANCE_STYLES[skill.clearance] || CLEARANCE_STYLES.LOW
  const src = SOURCE_STYLES[skill.source] || SOURCE_STYLES.builtin
  const SrcIcon = src.icon

  return (
    <div className="card p-4 space-y-2 hover:shadow-card-hover transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-gray-800 truncate">{skill.name}</div>
          {skill.version && <div className="text-xs text-gray-400 mt-0.5">v{skill.version}</div>}
        </div>
        <div className="flex flex-col gap-1 items-end shrink-0">
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${cl.bg} ${cl.text}`}>
            <Shield size={10} className="inline mr-0.5" />{cl.label}
          </span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${src.bg} ${src.text}`}>
            <SrcIcon size={10} className="inline mr-0.5" />{src.label}
          </span>
        </div>
      </div>
      {skill.author && (
        <div className="text-xs text-gray-500">作者: {skill.author}</div>
      )}
      <div className="flex items-center gap-2 text-xs">
        <span className="text-gray-400">风险等级</span>
        <div className="flex gap-0.5">
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className={`w-2 h-2 rounded-full ${i <= skill.risk_level ? (
              skill.risk_level <= 2 ? 'bg-green-400' : skill.risk_level <= 3 ? 'bg-yellow-400' : 'bg-red-400'
            ) : 'bg-gray-200'}`} />
          ))}
        </div>
      </div>
      <div className="flex items-center gap-1 text-xs">
        {skill.status === 'loaded' ? (
          <><CheckCircle size={11} className="text-green-500" /><span className="text-green-600">已加载</span></>
        ) : (
          <><XCircle size={11} className="text-red-500" /><span className="text-red-600">{skill.status}</span></>
        )}
      </div>
    </div>
  )
}

export default function SkillsHub() {
  const pollingEnabled  = useAppStore((s) => s.pollingEnabled)
  const pollingInterval = useAppStore((s) => s.pollingInterval)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await fetchSkillsHubStats()
      setData(result)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    if (!pollingEnabled) return
    const timer = setInterval(load, pollingInterval * 1000)
    return () => clearInterval(timer)
  }, [load, pollingEnabled, pollingInterval])

  const skills = data?.skills ?? []
  const filtered = filter
    ? skills.filter(s => s.name.toLowerCase().includes(filter.toLowerCase()) || s.source.toLowerCase().includes(filter.toLowerCase()))
    : skills

  // 按来源分组
  const builtinSkills = filtered.filter(s => s.source === 'builtin')
  const hubSkills = filtered.filter(s => s.source !== 'builtin')

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">技能中心</h1>
          <p className="text-sm text-gray-500 mt-0.5">技能管理 · 热加载 · 外部目录扫描</p>
        </div>
        <button className="btn-secondary" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      <PageHero
        icon={Package}
        eyebrow="技能与能力扩展"
        title={data?.hub_enabled ? 'Skills Hub 已启用' : 'Skills Hub 当前未启用'}
        description="集中查看内置技能、Hub / 外部技能来源、加载状态以及风险等级，方便统一收口技能侧能力。"
        gradientClass="from-emerald-50 via-white to-indigo-50"
        metaItems={[
          `技能总数：${data?.total_skills ?? 0}`,
          `已加载：${data?.loaded_skills ?? 0}`,
          `加载失败：${data?.failed_skills ?? 0}`,
          `过滤结果：${filtered.length}`,
        ]}
      />

      {error && <NoticeBanner variant="error">{error}</NoticeBanner>}

      {/* 概览统计 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card p-4 text-center">
          <div className={`text-2xl font-bold ${data?.hub_enabled ? 'text-green-600' : 'text-gray-400'}`}>
            {data?.hub_enabled ? '开启' : '关闭'}
          </div>
          <div className="text-xs text-gray-500 mt-1">Hub 状态</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold text-blue-600">{data?.total_skills ?? '—'}</div>
          <div className="text-xs text-gray-500 mt-1">总技能数</div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-2xl font-bold text-green-600">{data?.loaded_skills ?? '—'}</div>
          <div className="text-xs text-gray-500 mt-1">已加载</div>
        </div>
        <div className="card p-4 text-center">
          <div className={`text-2xl font-bold ${data?.failed_skills > 0 ? 'text-red-500' : 'text-gray-400'}`}>
            {data?.failed_skills ?? 0}
          </div>
          <div className="text-xs text-gray-500 mt-1">加载失败</div>
        </div>
      </div>

      {/* 外部扫描目录 */}
      {data?.external_dirs_scanned && data.external_dirs_scanned.length > 0 && (
        <div className="card p-3">
          <div className="text-xs font-medium text-gray-600 mb-2">已扫描的外部目录</div>
          <div className="flex flex-wrap gap-2">
            {data.external_dirs_scanned.map((d, i) => (
              <span key={i} className="inline-flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded">
                <FolderOpen size={11} />{d}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 搜索 */}
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
        <input
          className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-200"
          placeholder="搜索技能名称或来源…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>

      {/* 内置技能 */}
      {builtinSkills.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            <Package size={14} className="inline mr-1" />内置技能 ({builtinSkills.length})
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {builtinSkills.map(s => <SkillCard key={s.name} skill={s} />)}
          </div>
        </div>
      )}

      {/* Hub / 外部技能 */}
      {hubSkills.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            <Globe size={14} className="inline mr-1" />Hub / 外部技能 ({hubSkills.length})
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {hubSkills.map(s => <SkillCard key={s.name} skill={s} />)}
          </div>
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <EmptyStateCard
          title={filter ? '没有匹配的技能' : '暂无已加载技能'}
          hint={filter ? '请尝试更换关键字。' : '等待 SkillsHub 拉取后将展示在此。'}
          className="py-12"
        />
      )}
    </div>
  )
}
