import type { RouteObject } from 'react-router-dom'

import { AppShell } from '@/components/layout/AppShell'
import { AnalysesPage } from '@/pages/Analyses'
import { AnalysisDetailPage } from '@/pages/AnalysisDetail'
import { ComparisonsPage } from '@/pages/Comparisons'
import { DashboardPage } from '@/pages/Dashboard'
import { ImprovementPathsPage } from '@/pages/ImprovementPaths'
import { NewAnalysisPage } from '@/pages/NewAnalysis'
import { NotFoundPage } from '@/pages/NotFound'
import { PlayerProfilePage } from '@/pages/PlayerProfile'
import { ProgressPage } from '@/pages/Progress'
import { SystemStatusPage } from '@/pages/SystemStatus'

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/analyses', element: <AnalysesPage /> },
      { path: '/analyses/new', element: <NewAnalysisPage /> },
      { path: '/analyses/:id', element: <AnalysisDetailPage /> },
      { path: '/progress', element: <ProgressPage /> },
      { path: '/comparisons', element: <ComparisonsPage /> },
      { path: '/improvement-paths', element: <ImprovementPathsPage /> },
      { path: '/profile', element: <PlayerProfilePage /> },
      { path: '/system', element: <SystemStatusPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
