import { useMemo, useState } from 'react'
import useAppStore from '../../store/useAppStore.js'
import {
  switchSysMode,
  updateAutoRules,
} from './api.js'
import {
  buildRuntimeModeAutoRulePatch,
  buildRuntimeModeSwitchConfirmMessage,
  buildRuntimeModeSwitchPayload,
  buildRuntimeModeViewModel,
} from './runtimeModeModel.js'
import { useRuntimeModeConsoleQuery } from './runtimeModeQueries.js'

export function useRuntimeModeController() {
  const pollingEnabled = useAppStore((state) => state.pollingEnabled)
  const pollingInterval = useAppStore((state) => state.pollingInterval)
  const pollingTick = useAppStore((state) => state.pollingTick)
  const [switching, setSwitching] = useState(false)
  const [switchResult, setSwitchResult] = useState(null)

  const {
    statusData: status,
    historyData: history,
    consoleError,
    loading,
    refreshing,
    stale,
    lastUpdatedAt,
    refetch,
  } = useRuntimeModeConsoleQuery({
    pollingEnabled,
    pollingTick,
    pollingInterval,
  })

  const viewModel = useMemo(() => buildRuntimeModeViewModel({
    status,
    history,
    loading,
    refreshing,
    consoleError,
    stale,
    lastUpdatedAt,
  }), [consoleError, history, lastUpdatedAt, loading, refreshing, stale, status])

  const handleSwitch = async (targetMode) => {
    if (!window.confirm(buildRuntimeModeSwitchConfirmMessage(targetMode))) {
      return
    }

    setSwitching(true)
    setSwitchResult(null)
    try {
      const result = await switchSysMode(buildRuntimeModeSwitchPayload(targetMode))
      setSwitchResult(result)
      await refetch()
    } catch (switchError) {
      setSwitchResult({ success: false, error: switchError.message || '切换失败' })
    } finally {
      setSwitching(false)
      setTimeout(() => setSwitchResult(null), 4000)
    }
  }

  const handleToggleRule = async (ruleKey, currentValue) => {
    try {
      await updateAutoRules(buildRuntimeModeAutoRulePatch(ruleKey, currentValue))
      await refetch()
    } catch (updateError) {
      console.warn('Failed to update auto rule:', updateError)
    }
  }

  return {
    ...viewModel,
    consoleError,
    history,
    lastUpdatedAt,
    loading,
    refreshing,
    status,
    switchResult,
    switching,
    onRefresh: refetch,
    onSwitchMode: handleSwitch,
    onToggleRule: handleToggleRule,
  }
}
