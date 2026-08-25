
import BondDashboardSurface from '../features/life/components/BondDashboardSurface.jsx'
import { useBondDashboardController } from '../features/life/bondDashboardController.js'

export default function Bond() {
  const bondDashboard = useBondDashboardController()
  return <BondDashboardSurface {...bondDashboard} />
}
