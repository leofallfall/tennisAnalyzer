import type { Analysis } from '@tpa/contracts'
import { Columns2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { EmptyState, PageHeader } from '@/components/common'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { api } from '@/lib/api'
import { useAsync } from '@/lib/hooks'

const MAX = 3

/**
 * Side-by-side viewing of processed executions. This is real (videos + skeletons);
 * what does NOT exist yet is automatic alignment, normalisation and difference analysis.
 */
export function ComparisonsPage() {
  const list = useAsync(() => api.listAnalyses(), [])
  const completed = (list.data ?? []).filter((a) => a.status === 'completed')
  const [selected, setSelected] = useState<string[]>([])
  const [details, setDetails] = useState<Record<string, Analysis>>({})

  useEffect(() => {
    for (const id of selected) {
      if (!details[id]) {
        api
          .getAnalysis(id)
          .then((a) => setDetails((d) => ({ ...d, [id]: a })))
          .catch(() => undefined)
      }
    }
  }, [selected, details])

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-MAX)))

  return (
    <>
      <PageHeader
        title="Comparisons"
        description="View up to three processed executions side by side, frame by frame."
      />
      <Card className="mb-4">
        <CardHeader>
          <CardTitle>What works today</CardTitle>
          <CardDescription>
            Manual side-by-side playback with skeleton overlays. Not yet available: automatic stroke
            alignment, camera compatibility checks, pose normalisation, professional reference
            library and difference highlighting.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {completed.length === 0 ? (
            <EmptyState icon={Columns2} title="No processed analyses yet" className="py-6">
              Process at least one video to compare executions.
            </EmptyState>
          ) : (
            <div className="flex flex-wrap gap-2">
              {completed.map((a) => (
                <label
                  key={a.id}
                  className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm has-checked:border-primary"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(a.id)}
                    onChange={() => toggle(a.id)}
                  />
                  <span className="max-w-[240px] truncate">{a.title}</span>
                </label>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {selected.map((id) => {
          const a = details[id]
          const v = a?.videos[0]
          const report = v ? a?.results?.videos[v.video_id] : undefined
          if (!a || !v || !report || !v.artifacts.proxy) return null
          return <ComparisonPane key={id} analysis={a} />
        })}
      </div>
    </>
  )
}

function ComparisonPane({ analysis }: { analysis: Analysis }) {
  const v = analysis.videos[0]!
  const report = analysis.results!.videos[v.video_id]!
  const pose = useAsync(
    () => (v.artifacts.pose ? api.getPose(v.artifacts.pose) : Promise.resolve(null)),
    [v.artifacts.pose],
  )
  const meta = report.preprocess.metadata
  return (
    <Card>
      <CardHeader>
        <CardTitle className="truncate">{analysis.title}</CardTitle>
      </CardHeader>
      <CardContent>
        <VideoPlayer
          src={v.artifacts.proxy!}
          fps={meta.average_fps ?? meta.nominal_fps ?? 30}
          frameCount={meta.frame_count}
          pose={pose.data}
        />
      </CardContent>
    </Card>
  )
}
