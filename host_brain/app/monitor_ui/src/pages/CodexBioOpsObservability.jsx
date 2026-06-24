import React from 'react'
import CodexBioOpsObservabilitySurface from '../features/observability/components/bioOps/CodexBioOpsObservabilitySurface.jsx'
import { useCodexBioOpsController } from '../features/observability/codexBioOpsController.js'

export default function CodexBioOpsObservability() {
  const bioOps = useCodexBioOpsController()
  return <CodexBioOpsObservabilitySurface {...bioOps} />
}
