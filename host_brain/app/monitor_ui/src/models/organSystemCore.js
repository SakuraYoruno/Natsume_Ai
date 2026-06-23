import { LOAD_STATES } from './loadState.js'
import {
  LIFE_BODY_STATUS_CLASSES,
  LIFE_BODY_STATUS_LABELS,
} from '../shared/statusMeta.js'

export { LOAD_STATES }

export const ORGAN_STATUS = Object.freeze({
  CRITICAL: 'critical',
  WARNING: 'warning',
  STABLE: 'stable',
  PENDING: 'pending',
})

export function clampPercent(value, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return '--'
  return `${Math.max(0, Math.min(100, Number(value))).toFixed(digits)}%`
}

export function toFixed(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return '--'
  return Number(value).toFixed(digits)
}

export function metric(label, value, hint, tone = 'text-gray-900') {
  return {
    label,
    value: value == null || value === '' ? '--' : value,
    hint: hint == null || hint === '' ? '—' : hint,
    tone,
  }
}

export function getOrganStatusMeta(status) {
  const key = String(status || ORGAN_STATUS.PENDING).toLowerCase()
  return {
    label: LIFE_BODY_STATUS_LABELS[key] || LIFE_BODY_STATUS_LABELS.pending,
    className: LIFE_BODY_STATUS_CLASSES[key] || LIFE_BODY_STATUS_CLASSES.pending,
  }
}

export function createOrganSystemModel(input) {
  return {
    id: input.id,
    name: input.name,
    status: input.status || ORGAN_STATUS.PENDING,
    loadState: input.loadState || LOAD_STATES.UNAVAILABLE,
    freshness: input.freshness || null,
    trend: input.trend || '--',
    riskLevel: input.riskLevel || '--',
    summary: input.summary || '等待器官系统样本接入。',
    symptoms: Array.isArray(input.symptoms) ? input.symptoms.filter(Boolean) : [],
    downstreamImpacts: Array.isArray(input.downstreamImpacts) ? input.downstreamImpacts.filter(Boolean) : [],
    metrics: Array.isArray(input.metrics) ? input.metrics.filter(Boolean) : [],
    representativeRefs: Array.isArray(input.representativeRefs) ? input.representativeRefs.filter(Boolean) : [],
    drilldownPath: input.drilldownPath || '/',
    drilldownLabel: input.drilldownLabel || '进入详情',
  }
}
