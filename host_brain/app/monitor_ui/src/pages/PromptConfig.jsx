import React from 'react'
import PromptConfigSurface from '../features/operations/components/promptOps/PromptConfigSurface.jsx'
import { usePromptConfigController } from '../features/operations/promptConfigController.js'

export default function PromptConfig() {
  const promptConfig = usePromptConfigController()

  return <PromptConfigSurface {...promptConfig} />
}
