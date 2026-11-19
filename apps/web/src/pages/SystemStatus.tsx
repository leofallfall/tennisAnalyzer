import type { AnalysisSummary, ModuleStatus } from '@tpa/contracts'
import { CheckCircle2, XCircle } from 'lucide-react'
import { Link } from 'react-router-dom'

import { ErrorAlert, KeyValue, PageHeader } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatDateTime, humanize } from '@/lib/format'
import { useAsync } from '@/lib/hooks'

const variant: Record<ModuleStatus, 'success' | 'warning' | 'neutral'> = {
  implemented: 'success',
  experimental: 'warning',
  not_implemented: 'neutral',
}

const RECENT_ERRORS = 5

/** Most recent failed analyses, newest first (M1.10). */
function recentFailures(analyses: AnalysisSummary[], limit = RECENT_ERRORS) {
  const when = (a: AnalysisSummary) => a.finished_at ?? a.updated_at
  return analyses
    .filter((a) => a.status === 'failed')
    .sort((a, b) => Date.parse(when(b)) - Date.parse(when(a)))
    .slice(0, limit)
}

function RecentErrorsCard() {
  const { data, error } = useAsync(() => api.listAnalyses(), [])
  const failures = data ? recentFailures(data) : []
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Recent processing errors</CardTitle>
        <CardDescription>
          The last {RECENT_ERRORS} failed analyses. Server logs hold the full details.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && <ErrorAlert title="Could not load analyses" message={error.message} />}
        {data && failures.length === 0 && (
          <p className="text-sm text-muted-foreground">No failed analyses.</p>
        )}
        {failures.length > 0 && (
          <ul className="divide-y" aria-label="Recent processing errors">
            {failures.map((a) => (
              <li key={a.id} className="flex flex-col gap-0.5 py-2">
                <div className="flex items-center justify-between gap-3">
                  <Link to={`/analyses/${a.id}`} className="text-sm font-medium hover:underline">
                    {a.title}
                  </Link>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatDateTime(a.finished_at ?? a.updated_at)}
                  </span>
                </div>
                <p className="text-xs">
                  <code className="font-mono text-danger">{a.error?.code ?? 'unknown_error'}</code>
                  {a.error?.message ? ` — ${a.error.message}` : ''}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

export function SystemStatusPage() {
  const { data, error } = useAsync(() => api.systemInfo(), [])
  return (
    <>
      <PageHeader
        title="System status"
        description="What this installation can and cannot do today."
      />
      {error && <ErrorAlert title="API unreachable" message={error.message} />}
      {data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Engine capabilities</CardTitle>
              <CardDescription>
                Reported by the backend; unimplemented modules never return results.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="divide-y">
                {data.capabilities.map((c) => (
                  <li key={c.module} className="flex items-start justify-between gap-3 py-2">
                    <div>
                      <p className="text-sm font-medium">{humanize(c.module)}</p>
                      <p className="text-xs text-muted-foreground">{c.description}</p>
                    </div>
                    <Badge variant={variant[c.status]}>{humanize(c.status)}</Badge>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Environment</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <KeyValue
                items={[
                  ['API', data.api_version],
                  ['Engine', data.engine_version],
                  ['Python', data.python_version],
                  ['Database', data.database],
                  ['Storage', data.storage_writable ? 'Writable' : 'Not writable'],
                ]}
              />
              <ul className="flex flex-col gap-1.5">
                {[...data.tools, data.pose_model, data.pose_runtime].map((t) => (
                  <li key={t.name} className="flex items-center gap-2 text-sm">
                    {t.available ? (
                      <CheckCircle2 className="size-4 text-success" aria-label="available" />
                    ) : (
                      <XCircle className="size-4 text-danger" aria-label="missing" />
                    )}
                    <span className="font-medium">{t.name}</span>
                    <span className="truncate text-xs text-muted-foreground">
                      {t.version ?? (t.available ? '' : 'missing')}
                    </span>
                  </li>
                ))}
              </ul>
              {!data.pose_runtime.available && data.pose_runtime.detail && (
                <p className="text-xs text-danger" role="alert">
                  Pose estimation cannot run: {data.pose_runtime.detail}
                </p>
              )}
              {!data.pose_model.available && (
                <p className="text-xs text-danger">
                  Pose model missing. Run{' '}
                  <code className="font-mono">uv run python scripts/download_models.py</code>
                </p>
              )}
            </CardContent>
          </Card>
          <RecentErrorsCard />
        </div>
      )}
    </>
  )
}
