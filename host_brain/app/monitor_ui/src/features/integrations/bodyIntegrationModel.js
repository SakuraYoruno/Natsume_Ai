function countItems(value) {
  return Array.isArray(value) ? value.length : 0
}

function hasText(value) {
  return Boolean(String(value || '').trim())
}

export function buildBodyIntegrationModel({
  action = 'state',
  bundle = null,
  loading = false,
  result = null,
  sayText = '',
  sending = false,
} = {}) {
  const health = bundle?.health || null
  const capabilities = bundle?.capabilities || null
  const errors = Array.isArray(bundle?.errors) ? bundle.errors : []
  const actionCount = countItems(capabilities?.actions)
  const eventCount = countItems(capabilities?.events)
  const healthReady = Boolean(health?.ok)
  const catalogReady = Boolean(capabilities) && actionCount > 0
  const sayReady = action !== 'say' || hasText(sayText)
  const canSend = Boolean(healthReady && catalogReady && sayReady && !sending)
  const degraded = errors.length > 0 || !healthReady || !catalogReady
  const dispatchFailed = Boolean(result && !result.ok)

  let headline = '先确认身体桥是否在线'
  let description = 'MateEngine 健康和动作目录都可读后，再通过右侧 Action Console 发送显式动作。'
  let focus = {
    kind: 'refresh',
    tone: 'degraded',
    title: '刷新 Body Bridge 状态',
    detail: '当前还没有可确认的桥接状态，先重新同步 MateEngine health 和 capabilities。',
    label: '刷新状态',
  }

  if (loading) {
    headline = '正在同步身体桥状态'
    description = '保留当前控制台输入，等待 health 与 capabilities 返回后再开放发送判断。'
    focus = {
      kind: 'noop',
      tone: 'syncing',
      title: '等待状态返回',
      detail: '正在读取 MateEngine health 和 capabilities。',
      label: '同步中',
    }
  } else if (!healthReady) {
    headline = 'MateEngine bridge 未确认在线'
    description = '发送动作前必须先拿到健康状态。当前只展示降级信息，不建议继续写入。'
    focus = {
      kind: 'refresh',
      tone: 'blocked',
      title: '先恢复 MateEngine health',
      detail: '健康接口未返回 ok，刷新或检查本地 MateEngine 服务。',
      label: '刷新状态',
    }
  } else if (!catalogReady) {
    headline = '动作目录缺失，先补齐 capabilities'
    description = 'Health 已返回，但动作目录为空。发送前需要知道后端支持哪些动作。'
    focus = {
      kind: 'refresh',
      tone: 'degraded',
      title: '重新读取 capabilities',
      detail: '刷新动作和事件目录，避免向未知能力发送动作。',
      label: '刷新目录',
    }
  } else if (!sayReady) {
    headline = 'Say 动作需要先填写文本'
    description = '当前动作选择为 say。填写要播报的文本后，发送按钮才会开放。'
    focus = {
      kind: 'focusText',
      tone: 'blocked',
      title: '填写 Say 文本',
      detail: '文本为空时不会调用 /v1/mateengine/say。',
      label: '定位文本框',
    }
  } else if (dispatchFailed) {
    headline = '最近一次身体动作发送失败'
    description = '桥接前置条件可读，但最近一次写入没有被 MateEngine 接收。先核对错误，再重新发送。'
    focus = {
      kind: 'focusConsole',
      tone: 'degraded',
      title: '复核失败结果',
      detail: result.error || '最近一次 MateEngine 写入失败。',
      label: '定位动作控制台',
    }
  } else {
    headline = '身体动作链路可发送'
    description = '健康、动作目录和当前输入均已满足。发送前再核对 action 类型和文本。'
    focus = {
      kind: 'focusConsole',
      tone: 'ready',
      title: `复核 ${action} 动作`,
      detail: action === 'say'
        ? '将通过 MateEngine say 接口发送文本。'
        : '将通过 MateEngine action 接口发送显式动作。',
      label: '定位动作控制台',
    }
  }

  return {
    status: degraded || dispatchFailed ? 'degraded' : 'ready',
    canSend,
    headline,
    description,
    focus,
    summary: {
      healthLabel: healthReady ? 'bridge ok' : 'bridge degraded',
      hostLabel: `${health?.host || '127.0.0.1'}:${health?.port || '18770'}`,
      actionCount,
      eventCount,
      errorCount: errors.length,
    },
    handoffSteps: [
      {
        key: 'health',
        label: 'Bridge Health',
        value: loading ? 'syncing' : (healthReady ? 'ok' : 'degraded'),
        detail: healthReady ? 'MateEngine health 已确认在线。' : (loading ? '正在同步 health。' : 'health 未确认，写入保持锁定。'),
        state: healthReady ? 'ready' : (loading ? 'warning' : 'blocked'),
      },
      {
        key: 'catalog',
        label: 'Capability Catalog',
        value: catalogReady ? `${actionCount} actions` : 'missing',
        detail: catalogReady ? `${eventCount} 个事件类型可用于后续观测。` : 'capabilities 缺失时不能确认动作是否受支持。',
        state: catalogReady ? 'ready' : (capabilities ? 'warning' : 'blocked'),
      },
      {
        key: 'intent',
        label: 'Current Intent',
        value: action === 'say' ? (sayReady ? 'say text ready' : 'say text empty') : action,
        detail: action === 'say' ? 'say 会调用文本播报接口。' : '非 say 动作会走显式 action 接口。',
        state: sayReady ? 'ready' : 'blocked',
      },
      {
        key: 'result',
        label: 'Dispatch Result',
        value: result?.ok
          ? (result.data?.request_id || 'accepted')
          : result
            ? 'failed'
            : sending
              ? 'sending'
              : canSend
                ? 'ready to send'
                : 'locked',
        detail: result?.ok
          ? '最近一次写入已返回 accepted/request_id。'
          : result
            ? '最近一次写入失败，先查看错误再重试。'
            : canSend
              ? '发送按钮已开放，但尚未写入。'
              : 'gate 未满足时不会调用 MateEngine 写入接口。',
        state: result?.ok ? 'ready' : result ? 'blocked' : canSend ? 'warning' : 'blocked',
      },
    ],
    gates: [
      {
        label: 'Health',
        state: healthReady ? 'ready' : (loading ? 'warning' : 'blocked'),
        detail: healthReady ? 'MateEngine health 已返回 ok。' : (loading ? '正在读取 health。' : 'health 未返回 ok。'),
      },
      {
        label: 'Actions',
        state: catalogReady ? 'ready' : (capabilities ? 'warning' : 'blocked'),
        detail: catalogReady ? `${actionCount} 个动作可见。` : '动作目录为空或不可读。',
      },
      {
        label: 'Events',
        state: eventCount > 0 ? 'ready' : (capabilities ? 'warning' : 'blocked'),
        detail: eventCount > 0 ? `${eventCount} 个事件类型可见。` : '事件目录暂无样本。',
      },
      {
        label: 'Input',
        state: sayReady ? 'ready' : 'blocked',
        detail: sayReady ? '当前动作输入可发送。' : 'Say 文本为空。',
      },
    ],
    cards: [
      {
        label: 'Bridge',
        value: healthReady ? 'OK' : 'Degraded',
        detail: healthReady ? 'MateEngine bridge 可以接收显式动作。' : '健康状态未确认。',
        tone: healthReady ? 'ready' : 'blocked',
      },
      {
        label: 'Action Catalog',
        value: String(actionCount),
        detail: actionCount > 0 ? '动作目录可用于选择写入类型。' : '当前未返回动作目录。',
        tone: actionCount > 0 ? 'ready' : 'degraded',
      },
      {
        label: 'Event Catalog',
        value: String(eventCount),
        detail: eventCount > 0 ? '事件类型可用于后续观测联动。' : '当前没有事件样本。',
        tone: eventCount > 0 ? 'ready' : 'degraded',
      },
    ],
  }
}
