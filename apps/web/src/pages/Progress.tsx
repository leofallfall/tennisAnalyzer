import { BarChart3 } from 'lucide-react'

import { EmptyState, PageHeader } from '@/components/common'
import { Card, CardContent } from '@/components/ui/card'

export function ProgressPage() {
  return (
    <>
      <PageHeader
        title="Progress"
        description="Longitudinal comparison of technical measurements across sessions."
      />
      <Card>
        <CardContent className="pt-4 sm:pt-5">
          <EmptyState icon={BarChart3} title="Progress tracking is not available yet">
            <p>
              It depends on stroke detection and stroke-level metrics. When available, this page
              will show which aspects improved, which issues persist or became more frequent, and —
              importantly — which apparent changes may be caused by different recording conditions
              rather than by your technique. Only measurements that are observable and comparable
              across sessions will be compared.
            </p>
          </EmptyState>
        </CardContent>
      </Card>
    </>
  )
}
