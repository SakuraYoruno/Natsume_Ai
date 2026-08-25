import MemoryBrowserSurface from '../features/life/components/MemoryBrowserSurface.jsx'
import { useMemoryBrowserController } from '../features/life/memoryBrowserController.js'

export default function MemoryBrowser() {
  const memoryBrowser = useMemoryBrowserController()
  return <MemoryBrowserSurface {...memoryBrowser} />
}
