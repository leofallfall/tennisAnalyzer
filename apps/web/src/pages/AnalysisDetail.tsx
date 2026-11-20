import type { Analysis, PoseFrame, PoseSequence, StrokeFilter } from '@tpa/contracts'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { AiAnalysisPanel } from '@/components/analysis/AiAnalysisPanel'
import {
  JointTable,
  PoseSummaryCard,
  ProcessingStatusCard,
  QualityCard,
  VideoInfoCard,
} from '@/components/analysis/panels'
import {
  ErrorAlert,
  ObservabilityBadge,
  PageHeader,
  PlaceholderNotice,
  PlaceholderSection,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Segmented } from '@/components/ui/segmented'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { VideoPlayer } from '@/components/video/VideoPlayer'
import { api } from '@/lib/api'
import {
  EXAMPLE_ISSUES,
  EXAMPLE_MEASUREMENTS,
  EXAMPLE_PHASES,
  EXAMPLE_SESSION_SUMMARY,
} from '@/lib/exampleData'
import { formatClock, formatDateTime, humanize } from '@/lib/format'
import { useAsync, useInterval } from '@/lib/hooks'
import { cn } from '@/lib/utils'

export function AnalysisDetailPage() {
  const { id = '' } = useParams()
  const { data: analysis, error, reload, loading } = useAsync(() => api.getAnalysis(id), [id])
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const busy = analysis?.status === 'queued' || analysis?.status === 'running'

  // Poll status cheaply while processing; reload the full analysis on change.
  useInterval(
    () => {
      if (!analysis) return
      api
        .getStatus(analysis.id)
        .then((s) => {
          if (s.status !== analysis.status || s.progress !== analysis.progress) reload()
        })
        .catch(() => undefined)
    },
    1500,
    busy,
  )

  const start = async () => {
    setStarting(true)
    setStartError(null)
    try {
      await api.startAnalysis(id)
      reload()
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  if (error) return <ErrorAlert title="Could not load analysis" message={error.message} />
  if (!analysis) return loading ? <p className="text-sm text-muted-foreground">Loading…</p> : null

  return (
    <>
      <Link
        to="/analyses"
        className="mb-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> My Analyses
      </Link>
      <PageHeader
        title={analysis.title}
        description={
          <>
            {humanize(analysis.mode)} analysis · created {formatDateTime(analysis.created_at)}
            {analysis.videos.length > 1 && <> · {humanize(analysis.video_relation)}</>}
            {' · '}dominant hand:{' '}
            {analysis.dominant_hand ? humanize(analysis.dominant_hand) : 'not specified'}
            {analysis.backhand && <> · {humanize(analysis.backhand)} backhand</>}
          </>
        }
      />
      {startError && <ErrorAlert title="Could not start" message={startError} />}
      <AnalysisBody key={analysis.id} analysis={analysis} onStart={start} starting={starting} />
    </>
  )
}

function AnalysisBody({
  analysis,
  onStart,
  starting,
}: {
  analysis: Analysis
  onStart: () => void
  starting: boolean
}) {
  const [videoIdx, setVideoIdx] = useState(0)
  const av = analysis.videos[videoIdx] ?? analysis.videos[0]!
  const report = analysis.results?.videos[av.video_id] ?? null
  const { data: video } = useAsync(() => api.getVideo(av.video_id), [av.video_id])
  const pose = useAsync<PoseSequence | null>(
    () => (av.artifacts.pose ? api.getPose(av.artifacts.pose) : Promise.resolve(null)),
    [av.artifacts.pose],
  )
  const [frameInfo, setFrameInfo] = useState<{ frame: number; pose: PoseFrame | null }>({
    frame: 0,
    pose: null,
  })
  const onFrameChange = useCallback(
    (frame: number, p: PoseFrame | null) => setFrameInfo({ frame, pose: p }),
    [],
  )

  // The proxy is browser-playable and CFR; before processing, fall back to the original file.
  const src = av.artifacts.proxy ?? av.file_url
  const meta = report?.preprocess.metadata ?? video?.metadata
  const fps = meta?.average_fps ?? meta?.nominal_fps ?? 30
  const duration = meta?.duration_s ?? 0

  return (
    <div className="flex flex-col gap-4">
      {analysis.videos.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {analysis.videos.map((v, i) => (
            <Button
              key={v.video_id}
              size="sm"
              variant={i === videoIdx ? 'default' : 'outline'}
              onClick={() => setVideoIdx(i)}
            >
              {humanize(v.camera_view === 'unknown' ? `video ${i + 1}` : v.camera_view)}
            </Button>
          ))}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle>Video</CardTitle>
              <CardDescription>
                {av.artifacts.proxy
                  ? 'Normalised proxy (rotation applied, constant frame rate)'
                  : 'Original upload'}
                {!av.artifacts.proxy &&
                  ' · if it does not play, your browser lacks the codec; processing creates a playable proxy'}
              </CardDescription>
            </div>
            {av.artifacts.overlay && (
              <Button asChild variant="outline" size="sm">
                <a href={av.artifacts.overlay} target="_blank" rel="noreferrer">
                  Overlay video <ExternalLink />
                </a>
              </Button>
            )}
          </CardHeader>
          <CardContent>
            <VideoPlayer
              key={src}
              src={src}
              fps={fps}
              frameCount={meta?.frame_count}
              pose={pose.data}
              segments={analysis.mode === 'single_stroke' ? EXAMPLE_PHASES(duration) : []}
              onFrameChange={onFrameChange}
            />
            {analysis.mode === 'single_stroke' && (
              <p className="mt-2 text-[11px] text-muted-foreground">
                Phase bar shows an <strong>example</strong> segmentation layout. Phase segmentation
                is not implemented yet.
              </p>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-col gap-4">
          <ProcessingStatusCard analysis={analysis} onStart={onStart} starting={starting} />
          {report && <QualityCard quality={report.quality} />}
          {report && <PoseSummaryCard summary={report.pose_summary} report={report} />}
          {!report && video && (
            <VideoInfoCard
              meta={video.metadata}
              issues={video.validation.issues}
              filename={video.original_filename}
            />
          )}
        </div>
      </div>

      {report && (
        <div className="grid gap-4 lg:grid-cols-2">
          <VideoInfoCard
            meta={report.source_metadata}
            issues={report.validation.issues}
            filename={av.original_filename}
          />
          <JointTable pose={frameInfo.pose} frame={frameInfo.frame} />
        </div>
      )}

      <AiAnalysisPanel
        analysis={analysis}
        currentTime={frameInfo.frame / fps}
        duration={duration}
        videoId={av.video_id}
      />

      {analysis.mode === 'session' ? <SessionPlaceholders /> : <SingleStrokePlaceholders />}
    </div>
  )
}

function SessionPlaceholders() {
  const [filter, setFilter] = useState<StrokeFilter>('both')
  const issues = EXAMPLE_ISSUES.filter((i) => filter === 'both' || i.stroke === filter)
  const s = EXAMPLE_SESSION_SUMMARY
  return (
    <Card>
      <CardHeader className="flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle>Session report</CardTitle>
          <CardDescription>
            Technical issues ranked by significance, with supporting strokes
          </CardDescription>
        </div>
        <Segmented
          ariaLabel="Stroke filter"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'both', label: 'Both' },
            { value: 'forehand', label: 'Forehand' },
            { value: 'backhand', label: 'Backhand' },
          ]}
        />
      </CardHeader>
      <CardContent>
        <PlaceholderNotice>
          Stroke detection, classification and issue identification are not implemented. The numbers
          below only illustrate how the session report will be structured. Frequencies always use
          the number of strokes where the metric is observable as the denominator.
        </PlaceholderNotice>
        <PlaceholderSection>
          <div className="mb-4 grid grid-cols-2 gap-2 p-1 sm:grid-cols-5">
            {(
              [
                ['Candidate strokes', s.candidateStrokes],
                ['Observable', s.observableStrokes],
                ['Forehands', s.forehands],
                ['Backhands', s.backhands],
                ['Excluded (quality)', s.excluded],
              ] as const
            ).map(([k, v]) => (
              <div key={k} className="rounded-md border bg-card p-3">
                <p className="text-[11px] text-muted-foreground">{k}</p>
                <p className="font-mono text-lg">{v}</p>
              </div>
            ))}
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Issue (example)</TH>
                <TH>Stroke</TH>
                <TH className="text-right">Occurrences / evaluable</TH>
                <TH>Confidence</TH>
                <TH>Type</TH>
                <TH>Evidence</TH>
              </tr>
            </THead>
            <TBody>
              {issues.map((i) => (
                <TR key={i.id} className="bg-card/80">
                  <TD>
                    <p className="font-medium">{i.title}</p>
                    <p className="text-xs text-muted-foreground">{i.significance}</p>
                    <p className="text-[11px] text-muted-foreground italic">
                      Limitation: {i.limitation}
                    </p>
                  </TD>
                  <TD>{humanize(i.stroke)}</TD>
                  <TD className="text-right font-mono whitespace-nowrap">
                    {i.occurrences} / {i.evaluable}{' '}
                    <span className="text-muted-foreground">
                      ({Math.round((i.occurrences / i.evaluable) * 100)}%)
                    </span>
                  </TD>
                  <TD>
                    <Badge
                      variant={
                        i.confidence === 'high'
                          ? 'success'
                          : i.confidence === 'medium'
                            ? 'warning'
                            : 'neutral'
                      }
                    >
                      {i.confidence}
                    </Badge>
                  </TD>
                  <TD>
                    <Badge variant={i.category === 'positive_reference' ? 'success' : 'neutral'}>
                      {humanize(i.category)}
                    </Badge>
                  </TD>
                  <TD className="font-mono text-xs whitespace-nowrap text-muted-foreground">
                    {i.timestamps.map((t) => formatClock(t, 1)).join(', ')}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </PlaceholderSection>
        <Button variant="outline" size="sm" className="mt-3" disabled>
          Open selected issues in Single Stroke Analysis (not available yet)
        </Button>
      </CardContent>
    </Card>
  )
}

function SingleStrokePlaceholders() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Biomechanical measurements</CardTitle>
          <CardDescription>Value, confidence and observability for every metric</CardDescription>
        </CardHeader>
        <CardContent>
          <PlaceholderNotice>
            Stroke-level metrics need phase segmentation, which is not implemented.
          </PlaceholderNotice>
          <PlaceholderSection>
            <Table>
              <THead>
                <tr>
                  <TH>Metric (example)</TH>
                  <TH className="text-right">Value</TH>
                  <TH>Confidence</TH>
                  <TH>Observability</TH>
                </tr>
              </THead>
              <TBody>
                {EXAMPLE_MEASUREMENTS.map((m) => (
                  <TR key={m.metric}>
                    <TD className="text-xs">{m.metric}</TD>
                    <TD
                      className={cn(
                        'text-right font-mono text-xs',
                        m.value === 'Unavailable' && 'text-muted-foreground',
                      )}
                    >
                      {m.value}
                    </TD>
                    <TD className="text-xs">{m.confidence}</TD>
                    <TD>
                      <ObservabilityBadge value={m.observability} />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </PlaceholderSection>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Technical feedback & evidence</CardTitle>
          <CardDescription>
            Observation → interpretation → recommendation, kept separate
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <PlaceholderNotice>
            AI interpretation is not implemented. Structure shown for layout only.
          </PlaceholderNotice>
          <PlaceholderSection>
            <div className="flex flex-col gap-2 p-1 text-sm">
              {(
                [
                  [
                    'Observation',
                    'Elbow angle at the estimated contact frame measured 142° (2D, side view).',
                  ],
                  [
                    'Interpretation',
                    'Arm may be moderately bent at contact; could be stylistic for a semi-western grip.',
                  ],
                  [
                    'Recommendation',
                    'Shadow swings focusing on extension through contact; compare with reference.',
                  ],
                ] as const
              ).map(([layer, text]) => (
                <div key={layer} className="rounded-md border bg-card p-2.5">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {layer}
                  </p>
                  <p className="text-xs">{text}</p>
                </div>
              ))}
              <div className="grid grid-cols-4 gap-2">
                {['Preparation', 'Backswing', 'Contact', 'Follow-through'].map((k) => (
                  <div
                    key={k}
                    className="flex aspect-video items-end rounded-md border bg-muted p-1 text-[10px] text-muted-foreground"
                  >
                    {k}
                  </div>
                ))}
              </div>
            </div>
          </PlaceholderSection>
          <Button variant="outline" size="sm" disabled className="self-start">
            Compare with another execution (not available yet)
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
