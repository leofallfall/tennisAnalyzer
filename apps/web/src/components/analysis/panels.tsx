import type {
  Analysis,
  PoseFrame,
  PoseSummary,
  ProcessingReport,
  QualityReport,
  ValidationIssue,
  VideoMetadata,
} from '@tpa/contracts'
import { Loader2, Play, RotateCcw } from 'lucide-react'

import {
  ErrorAlert,
  KeyValue,
  ObservabilityBadge,
  RatingBadge,
  StatusBadge,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatBytes, formatDuration, formatFps, formatPercent, humanize } from '@/lib/format'
import { KEY_JOINTS } from '@/lib/skeleton'

export function ProcessingStatusCard({
  analysis,
  onStart,
  starting,
}: {
  analysis: Analysis
  onStart: () => void
  starting: boolean
}) {
  const { status } = analysis
  const busy = status === 'queued' || status === 'running'
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle>Processing</CardTitle>
          <CardDescription>Ingestion, quality and pose estimation (Milestone 1)</CardDescription>
        </div>
        <StatusBadge status={status} />
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {busy && (
          <>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                {humanize(analysis.stage ?? status)}
              </span>
              <span className="font-mono">{formatPercent(analysis.progress)}</span>
            </div>
            <Progress value={analysis.progress} />
            {status === 'queued' && (
              <p className="text-xs text-muted-foreground">
                Waiting for the worker. If this does not change, make sure it is running:{' '}
                <code className="font-mono">uv run tpa-worker</code>
              </p>
            )}
          </>
        )}
        {status === 'failed' && analysis.error && (
          <ErrorAlert
            title="Processing failed"
            message={analysis.error.message}
            code={analysis.error.code}
          />
        )}
        {status === 'completed' && (
          <p className="text-sm text-muted-foreground">
            Video processed. Strokes come from an experimental detector (see the strokes panel); no
            biomechanical analysis has been performed — that module is not implemented yet.
          </p>
        )}
        {(status === 'created' || status === 'failed') && (
          <Button onClick={onStart} disabled={starting} className="self-start">
            {status === 'failed' ? <RotateCcw /> : <Play />}
            {status === 'failed' ? 'Retry processing' : 'Start processing'}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export function IssuesList({ issues }: { issues: ValidationIssue[] }) {
  if (!issues.length) return null
  return (
    <ul className="flex flex-col gap-1.5">
      {issues.map((i) => (
        <li key={i.code} className="flex items-start gap-2 text-xs">
          <Badge variant={i.severity === 'error' ? 'danger' : 'warning'}>{i.severity}</Badge>
          <span className="text-muted-foreground">{i.message}</span>
        </li>
      ))}
    </ul>
  )
}

export function VideoInfoCard({
  meta,
  issues,
  filename,
}: {
  meta: VideoMetadata
  issues: ValidationIssue[]
  filename: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Source video</CardTitle>
        <CardDescription className="truncate">{filename}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <KeyValue
          items={[
            ['Resolution', `${meta.display_width}×${meta.display_height}`],
            [
              'Orientation',
              meta.orientation ??
                (meta.display_width >= meta.display_height ? 'landscape' : 'portrait'),
            ],
            ['Frame rate', formatFps(meta.average_fps ?? meta.nominal_fps)],
            ['Variable frame rate', meta.is_variable_frame_rate ? 'yes' : 'no'],
            ['Rotation', `${meta.rotation_deg}°`],
            ['Duration', formatDuration(meta.duration_s)],
            ['Frames', meta.frame_count ?? '—'],
            ['Codec', meta.codec ?? '—'],
            ['Size', formatBytes(meta.file_size_bytes)],
          ]}
        />
        <IssuesList issues={issues} />
      </CardContent>
    </Card>
  )
}

export function QualityCard({ quality }: { quality: QualityReport }) {
  const rows: [string, QualityReport['resolution']][] = [
    ['Resolution', quality.resolution],
    ['Frame rate', quality.frame_rate],
    ['Sharpness', quality.blur],
    ['Lighting', quality.lighting],
    ['Player visibility', quality.visibility],
  ]
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div>
          <CardTitle>Video quality</CardTitle>
          <CardDescription>
            Heuristic thresholds, not yet calibrated on tennis footage
          </CardDescription>
        </div>
        <ObservabilityBadge value={quality.observability} />
      </CardHeader>
      <CardContent className="flex flex-col gap-2.5">
        {rows.map(([label, a]) => (
          <div key={label} className="flex flex-col gap-0.5">
            <div className="flex items-center justify-between text-sm">
              <span>{label}</span>
              <RatingBadge rating={a.rating} />
            </div>
            <p className="text-xs text-muted-foreground">{a.detail}</p>
          </div>
        ))}
        {quality.notes.map((n) => (
          <p key={n} className="text-xs font-medium text-danger">
            {n}
          </p>
        ))}
      </CardContent>
    </Card>
  )
}

export function PoseSummaryCard({
  summary,
  report,
}: {
  summary: PoseSummary
  report: ProcessingReport
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Pose estimation</CardTitle>
        <CardDescription>
          {report.pose_estimator} · {report.pose_model}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <KeyValue
          items={[
            ['Frames analysed', summary.frames_analysed],
            [
              'Frames with a pose',
              `${summary.frames_with_pose} (${formatPercent(summary.detection_rate)})`,
            ],
            [
              'Inference',
              summary.ms_per_frame ? `${summary.ms_per_frame.toFixed(1)} ms/frame` : '—',
            ],
            ['Total processing', formatDuration(report.total_duration_ms / 1000)],
          ]}
        />
      </CardContent>
    </Card>
  )
}

/** Real keypoint data for the frame currently shown in the player. */
export function JointTable({ pose, frame }: { pose: PoseFrame | null; frame: number }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Joint positions · frame {frame}</CardTitle>
        <CardDescription>
          Normalised image coordinates from the pose model. Visibility is the model&apos;s own
          score, not a calibrated accuracy.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!pose ? (
          <p className="text-sm text-muted-foreground">This frame was not analysed.</p>
        ) : !pose.detected ? (
          <p className="text-sm text-muted-foreground">No person detected in this frame.</p>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Joint</TH>
                <TH className="text-right">x</TH>
                <TH className="text-right">y</TH>
                <TH className="text-right">Visibility</TH>
              </tr>
            </THead>
            <TBody>
              {KEY_JOINTS.map((name) => {
                const k = pose.keypoints.find((p) => p.name === name)
                if (!k) return null
                const low = (k.visibility ?? 0) < 0.5
                return (
                  <TR key={name} className={low ? 'text-muted-foreground' : undefined}>
                    <TD className="text-xs">{humanize(name)}</TD>
                    <TD className="text-right font-mono text-xs">{k.x.toFixed(3)}</TD>
                    <TD className="text-right font-mono text-xs">{k.y.toFixed(3)}</TD>
                    <TD className="text-right font-mono text-xs">
                      {k.visibility == null ? '—' : k.visibility.toFixed(2)}
                      {low && ' ·low'}
                    </TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
