import RuntimeModeSurface from '../features/life/components/RuntimeModeSurface.jsx'
import { useRuntimeModeController } from '../features/life/runtimeModeController.js'

export default function RuntimeMode() {
  const runtimeMode = useRuntimeModeController()
  return <RuntimeModeSurface {...runtimeMode} />
}
