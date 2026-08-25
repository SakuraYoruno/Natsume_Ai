import DigitalLifeOverviewSurface from '../features/life/components/DigitalLifeOverviewSurface.jsx'
import { useDigitalLifeOverviewController } from '../features/life/digitalLifeOverviewController.js'

export default function DigitalLifeOverview() {
  const overview = useDigitalLifeOverviewController()

  return (
    <DigitalLifeOverviewSurface {...overview} />
  )
}
