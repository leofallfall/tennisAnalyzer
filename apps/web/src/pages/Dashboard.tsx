import { ArrowRight, ListVideo, Plus, Route, Target } from 'lucide-react'
import { Link } from 'react-router-dom'

import { EmptyState, ErrorAlert, PageHeader, StatusBadge } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatDateTime, humanize } from '@/lib/format'
import { useAsync } from '@/lib/hooks'
import { loadProfile } from '@/lib/profile'

export function DashboardPage() {
  const analyses = useAsync(() => api.listAnalyses(), [])
  const profile = loadProfile()
  const recent = analyses.data?.slice(0, 5) ?? []

  return (
    <>
      <PageHeader
        title={profile.name ? `Welcome back, ${profile.name}` : 'Welcome'}
        description="Your personal tennis technique laboratory. Upload a session or a single stroke to begin."
        actions={
          <Button asChild>
            <Link to="/analyses/new">
              <Plus /> New analysis
            </Link>
          </Button>
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent analyses</CardTitle>
            <Button asChild variant="link" size="sm">
              <Link to="/analyses">
                View all <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {analyses.error ? (
              <ErrorAlert title="Could not load analyses" message={analyses.error.message} />
            ) : recent.length === 0 && !analyses.loading ? (
              <EmptyState icon={ListVideo} title="No analyses yet">
                Start by uploading a practice session or a single stroke.
              </EmptyState>
            ) : (
              <ul className="divide-y">
                {recent.map((a) => (
                  <li key={a.id}>
                    <Link
                      to={`/analyses/${a.id}`}
                      className="flex items-center justify-between gap-3 py-2.5 hover:text-primary"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{a.title}</p>
                        <p className="text-xs text-muted-foreground">
                          {humanize(a.mode)} · {formatDateTime(a.created_at)}
                        </p>
                      </div>
                      <StatusBadge status={a.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Open technical issues</CardTitle>
            </CardHeader>
            <CardContent>
              <EmptyState icon={Target} title="No issues identified" className="py-6">
                Technical issues will appear once stroke detection and biomechanical analysis are
                implemented. Nothing is inferred until then.
              </EmptyState>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Active improvement paths</CardTitle>
            </CardHeader>
            <CardContent>
              <EmptyState icon={Route} title="No active paths" className="py-6">
                <Link className="text-primary hover:underline" to="/improvement-paths">
                  Learn about improvement paths
                </Link>
              </EmptyState>
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  )
}
