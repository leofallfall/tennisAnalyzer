import { Route } from 'lucide-react'

import { EmptyState, PageHeader } from '@/components/common'
import { Card, CardContent } from '@/components/ui/card'

export function ImprovementPathsPage() {
  return (
    <>
      <PageHeader
        title="Improvement Paths"
        description="Open-ended technical goals, e.g. “Improve forehand preparation”."
      />
      <Card>
        <CardContent className="pt-4 sm:pt-5">
          <EmptyState icon={Route} title="Improvement paths are not available yet">
            <p>
              You will be able to run several paths in parallel, each linked to identified issues,
              drills suggested by the AI Coach and the analyses that provide evidence of change.
              Paths stay open until you close them — no deadlines, calendars or reminders.
            </p>
          </EmptyState>
        </CardContent>
      </Card>
    </>
  )
}
