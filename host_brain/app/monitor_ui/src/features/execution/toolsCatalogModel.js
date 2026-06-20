import { flattenPentestTools } from '../../shared/pentestMeta.js'

export function getPentestToolMetadataView(capabilities) {
  const pentestTools = flattenPentestTools(capabilities)
  return {
    pentestTools,
    readonlyCount: pentestTools.filter((tool) => tool.readonly).length,
    sideEffectCount: pentestTools.filter((tool) => tool.side_effect).length,
    phaseCount: (capabilities?.phases || Object.keys(capabilities?.groups || {})).length || 0,
    totalCount: capabilities?.count ?? pentestTools.length,
  }
}

export function filterToolsBySideEffect(tools = [], filter = 'ALL') {
  const items = Array.isArray(tools) ? tools : []
  return items.filter((tool) => {
    if (filter === 'READ' && tool.side_effect_level !== 'READ') return false
    if (filter === 'WRITE' && tool.side_effect_level !== 'WRITE') return false
    return true
  })
}

export function getToolsCatalogCounts(tools = []) {
  const items = Array.isArray(tools) ? tools : []
  return {
    readCount: items.filter((tool) => tool.side_effect_level === 'READ').length,
    writeCount: items.filter((tool) => tool.side_effect_level === 'WRITE').length,
  }
}

export function reconcileSelectedTool({ selectedTool, executeTarget, tools = [] }) {
  if (!selectedTool) {
    return { selectedTool: null, executeTarget: null }
  }
  const nextSelectedTool = tools.find((tool) => tool.id === selectedTool.id) || null
  if (!nextSelectedTool) {
    return { selectedTool: null, executeTarget: null }
  }
  const nextExecuteTarget = executeTarget?.id === nextSelectedTool.id ? nextSelectedTool : executeTarget
  return {
    selectedTool: nextSelectedTool,
    executeTarget: nextExecuteTarget,
  }
}

export function buildToolsHeroMetaItems({ health, tools = [], readCount, writeCount, filter }) {
  return [
    `工具总数：${health?.catalog_size ?? tools.length}`,
    `只读工具：${readCount}`,
    `写入工具：${writeCount}`,
    `当前筛选：${filter}`,
  ]
}

export function buildToolExecutionRequest(tool, form) {
  let args = {}
  try {
    args = JSON.parse(form.args_json || '{}')
  } catch (error) {
    return { ok: false, error: `参数 JSON 不合法：${error.message}` }
  }

  if (tool?.side_effect_level === 'WRITE' && !form.approval_token_ref.trim()) {
    return { ok: false, error: '写入工具必须填写审批令牌引用。' }
  }

  return {
    ok: true,
    request: {
      tool_id: tool.id,
      intent_id: form.intent_id,
      session_id: form.session_id,
      args,
      approval_token_ref: form.approval_token_ref || undefined,
    },
  }
}
