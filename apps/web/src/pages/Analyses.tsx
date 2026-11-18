import { ListVideo, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'

import { EmptyState, ErrorAlert, PageHeader, StatusBadge } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { api } from '@/lib/api'
import { formatDateTime, humanize } from '@/lib/format'
import { useAsync } from '@/lib/hooks'

export function AnalysesPage() {
  const { data, error, loading } = useAsync(() => api.listAnalyses(), [])
  return (
    <>
      <PageHeader
        title="My Analyses"
        description="Every session and single-stroke analysis you have created."
        actions={
          <Button asChild>
            <Link to="/analyses/new">
              <Plus /> New analysis
            </Link>
          </Button>
        }
      />
      {error && <ErrorAlert title="Could not load analyses" message={error.message} />}
      {!error && !loading && data?.length === 0 && (
        <EmptyState
          icon={ListVideo}
          title="No analyses yet"
          action={
            <Button asChild>
              <Link to="/analyses/new">Create your first analysis</Link>
            </Button>
          }
        />
      )}
      {data && data.length > 0 && (
        <Card>
          <CardContent className="pt-2 sm:pt-2">
            <Table>
              <THead>
                <tr>
                  <TH>Title</TH>
                  <TH>Type</TH>
                  <TH>Videos</TH>
                  <TH>Created</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <TBody>
                {data.map((a) => (
                  <TR key={a.id}>
                    <TD className="max-w-[320px] truncate font-medium">
                      <Link to={`/analyses/${a.id}`} className="hover:text-primary hover:underline">
                        {a.title}
                      </Link>
                    </TD>
                    <TD className="text-muted-foreground">{humanize(a.mode)}</TD>
                    <TD className="text-muted-foreground">{a.video_count}</TD>
                    <TD className="whitespace-nowrap text-muted-foreground">
                      {formatDateTime(a.created_at)}
                    </TD>
                    <TD>
                      <StatusBadge status={a.status} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </>
  )
}
