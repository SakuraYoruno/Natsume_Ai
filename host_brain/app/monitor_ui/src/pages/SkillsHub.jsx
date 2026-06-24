import React from 'react'
import SkillsHubSurface from '../features/execution/components/SkillsHubSurface.jsx'
import { useSkillsHubController } from '../features/execution/skillsHubController.js'

export default function SkillsHub() {
  const skillsHub = useSkillsHubController()

  return <SkillsHubSurface {...skillsHub} />
}
