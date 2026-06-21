import CustomNode from './CustomNode.jsx'
import {
  getStableStageLabel,
  TRACE_STAGE_GROUPS,
  TRACE_TIMELINE_STAGE_ORDER,
} from './traceStageModel.js'
import { buildUnifiedTimelineItems as buildTraceUnifiedTimelineItems } from './traceUnifiedTimelineModel.js'
import {
  formatIntent,
  getEventPreview,
  getNodePreview,
  getSummaryInputText,
  getSummaryOutputText,
  getTribePayloadPreview,
  safeStringify,
  truncateText,
} from './tracePreviewModel.js'
import {
  ISSUE_STATUSES,
  RUNNING_STATUSES,
  buildImmuneStageMeta,
  buildRetrievalRuntimeMeta,
  buildStageSnapshots,
  buildStageTimelineSections,
  normalizeStatus,
} from './traceRequestRuntimeModel.js'
import { formatDateTime as formatSharedDateTime } from '../../../../shared/formatters.js'

const TRACE_NODE_TYPES = { customNode: CustomNode }
const TRACE_DETAIL_TIME_OPTIONS = Object.freeze({
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

function formatDateTime(tsMs) {
  return formatSharedDateTime(tsMs, {
    placeholder: '-',
    options: TRACE_DETAIL_TIME_OPTIONS,
  })
}

function buildUnifiedTimelineItems(nodes, tribeEvents) {
  return buildTraceUnifiedTimelineItems(nodes, tribeEvents, {
    getNodePreview,
    getTribePayloadPreview,
  })
}

export {
  ISSUE_STATUSES,
  RUNNING_STATUSES,
  TRACE_NODE_TYPES,
  TRACE_STAGE_GROUPS,
  TRACE_TIMELINE_STAGE_ORDER,
  buildImmuneStageMeta,
  buildRetrievalRuntimeMeta,
  buildStageSnapshots,
  buildStageTimelineSections,
  buildUnifiedTimelineItems,
  formatDateTime,
  formatIntent,
  getEventPreview,
  getNodePreview,
  getStableStageLabel,
  getSummaryInputText,
  getSummaryOutputText,
  getTribePayloadPreview,
  normalizeStatus,
  safeStringify,
  truncateText,
}
