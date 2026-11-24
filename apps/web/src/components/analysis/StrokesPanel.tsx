import type {
  Analysis,
  AnalysisVideo,
  NotAssessableStretch,
  RegionVisibility,
  RegionVisibilityState,
  StrokeEvent,
  StrokesView,
} from '@tpa/contracts'
import { useState } from 'react'

import { ErrorAlert } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatClock } from '@/lib/format'
import { useAsync } from '@/lib/hooks'
import { eventFrame, eventTime, timelineItems } from '@/lib/strokes'
import { cn } from '@/lib/utils'

/** Grey stripes: not the amber hatch used for example/placeholder content. */
const NA_STRIPES = {
  backgroundImage:
    'repeating-linear-gradient(135deg, color-mix(in oklch, var(--color-foreground) 16%, transparent) 0 4px, transparent 4px 8px)',
}

const STATE_BADGE = {
  flagged: 'primary',
  to_check: 'warning',
  discarded: 'neutral',
} as const

export function StrokesPanel({
  analysis,
  av,
  frame,
  duration,
  onSeekFrame,
  onDetected,
}: {
  analysis: Analysis
  av: AnalysisVideo
  /** Current proxy frame of the player. */
  frame: number
  duration: number
  onSeekFrame: (frame: number) => void
  onDetected: () => void
}) {
  const url = av.strokes_url ?? null
  const { data: view, error } = useAsync<StrokesView | null>(
    () => (url ? api.getStrokes(url) : Promise.resolve(null)),
    [url],
  )
  const [showDiscarded, setShowDiscarded] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)
  const busy = analysis.status === 'queued' || analysis.status === 'running'
  const detection = av.stroke_detection ?? analysis.results?.strokes?.[av.video_id] ?? null

  const detect = async () => {
    setDetecting(true)
    setDetectError(null)
    try {
      await api.detectStrokes(analysis.id)
      onDetected()
    } catch (e) {
      setDetectError(e instanceof Error ? e.message : String(e))
    } finally {
      setDetecting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Colpi sulla timeline <Badge variant="warning">sperimentale</Badge>
        </CardTitle>
        <CardDescription>
          Rilevatore automatico con soglie provvisorie, non validate. Clicca un colpo per andare a
          quel momento del video.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && <ErrorAlert title="Impossibile caricare i colpi" message={error.message} />}
        {detectError && <ErrorAlert title="Impossibile avviare" message={detectError} />}
        {!url && (
          <NoStrokes
            analysis={analysis}
            busy={busy}
            failed={detection?.status === 'failed' ? (detection.error?.message ?? '') : null}
            detecting={detecting}
            onDetect={detect}
          />
        )}
        {view && (
          <StrokesBody
            view={view}
            frame={frame}
            duration={duration}
            showDiscarded={showDiscarded}
            setShowDiscarded={setShowDiscarded}
            onSeekFrame={onSeekFrame}
          />
        )}
        {view && analysis.status === 'completed' && (
          <div>
            <Button size="sm" variant="ghost" disabled={detecting} onClick={detect}>
              Rileva di nuovo (usa la posa già calcolata)
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function NoStrokes({
  analysis,
  busy,
  failed,
  detecting,
  onDetect,
}: {
  analysis: Analysis
  busy: boolean
  failed: string | null
  detecting: boolean
  onDetect: () => void
}) {
  if (busy)
    return <p className="text-sm text-muted-foreground">I colpi compaiono a fine elaborazione.</p>
  if (analysis.status !== 'completed')
    return (
      <p className="text-sm text-muted-foreground">
        Avvia l'elaborazione: i colpi vengono rilevati dopo la stima della posa.
      </p>
    )
  return (
    <div className="flex flex-col items-start gap-2 text-sm">
      {failed != null ? (
        <ErrorAlert title="Rilevamento colpi non riuscito" message={failed} />
      ) : (
        <p className="text-muted-foreground">
          Colpi non ancora rilevati per questa analisi (elaborata prima del rilevatore).
        </p>
      )}
      <Button size="sm" disabled={detecting} onClick={onDetect}>
        Rileva colpi
      </Button>
      <p className="text-[11px] text-muted-foreground">
        Usa la posa già calcolata: non rifà la stima della posa.
      </p>
    </div>
  )
}

function StrokesBody({
  view,
  frame,
  duration,
  showDiscarded,
  setShowDiscarded,
  onSeekFrame,
}: {
  view: StrokesView
  frame: number
  duration: number
  showDiscarded: boolean
  setShowDiscarded: (v: boolean) => void
  onSeekFrame: (frame: number) => void
}) {
  const fps = view.fps
  const flagged = view.events.filter((e) => e.state === 'flagged')
  const toCheck = view.events.filter((e) => e.state === 'to_check')
  const discarded = view.events.filter((e) => e.state === 'discarded')
  const end = Math.max(
    duration,
    ...view.events.map((e) => e.window_end_s),
    ...view.not_assessable.map((s) => s.end_s),
    0.001,
  )
  const pct = (t: number) => `${Math.min(100, Math.max(0, (t / end) * 100))}%`
  const now = frame / fps
  const items = timelineItems(view, showDiscarded)
  const shownEvents = items.flatMap((i) => (i.kind === 'event' ? [i.event] : []))

  return (
    <>
      <p className="text-sm" data-testid="strokes-summary">
        <strong>{flagged.length}</strong> colpi segnalati · <strong>{toCheck.length}</strong> da
        verificare · <strong>{view.not_assessable.length}</strong> tratti non valutabili
        {discarded.length > 0 && (
          <span className="text-muted-foreground">
            {' '}
            · {discarded.length} scartati {showDiscarded ? '(mostrati)' : '(nascosti)'}
          </span>
        )}
      </p>

      <div
        className="relative h-9 rounded-sm bg-muted"
        role="group"
        aria-label="Timeline dei colpi"
      >
        {view.not_assessable.map((s) => (
          <button
            type="button"
            key={`na-${s.start_frame}`}
            title={`Non valutabile ${formatClock(s.start_s, 1)}–${formatClock(s.end_s, 1)}: ${s.reason}`}
            aria-label={`Non valutabile da ${formatClock(s.start_s, 1)} a ${formatClock(s.end_s, 1)}`}
            onClick={() => onSeekFrame(s.start_frame)}
            className="absolute inset-y-0 bg-foreground/5"
            style={{ ...NA_STRIPES, left: pct(s.start_s), width: pct(s.end_s - s.start_s) }}
          />
        ))}
        {shownEvents.map((e) => (
          <button
            type="button"
            key={e.id}
            title={`${e.label} ${formatClock(eventTime(e), 2)}${e.reasons.length ? `: ${e.reasons.join('; ')}` : ''}`}
            aria-label={`${e.label} a ${formatClock(eventTime(e), 2)}`}
            data-state={e.state}
            onClick={() => onSeekFrame(eventFrame(e, fps))}
            className={cn(
              'absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 hover:scale-150',
              e.state === 'flagged' && 'border-primary bg-primary',
              e.state === 'to_check' && 'border-warning bg-card',
              e.state === 'discarded' && 'size-2 border-muted-foreground/60 bg-muted-foreground/30',
            )}
            style={{ left: pct(eventTime(e)) }}
          />
        ))}
        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground"
          style={{ left: pct(now) }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1">
          <span className="size-2.5 rounded-full border-2 border-primary bg-primary" /> Colpo
          segnalato
        </span>
        <span className="flex items-center gap-1">
          <span className="size-2.5 rounded-full border-2 border-warning bg-card" /> Da verificare
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2.5 w-4 bg-foreground/5" style={NA_STRIPES} /> Non valutabile: evidenze
          insufficienti, non assenza di colpi
        </span>
        <label className="ml-auto flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showDiscarded}
            onChange={(e) => setShowDiscarded(e.target.checked)}
          />
          Mostra scartati
        </label>
      </div>
      <RegionLegend />

      <ol className="max-h-80 divide-y overflow-y-auto rounded-md border text-sm">
        {items.length === 0 && (
          <li className="p-3 text-muted-foreground">Nessun colpo da mostrare.</li>
        )}
        {items.map((i) =>
          i.kind === 'event' ? (
            <EventRow
              key={i.event.id}
              e={i.event}
              active={now >= i.event.window_start_s && now <= i.event.window_end_s}
              onClick={() => onSeekFrame(eventFrame(i.event, fps))}
            />
          ) : (
            <StretchRow
              key={`na-${i.stretch.start_frame}`}
              s={i.stretch}
              active={now >= i.stretch.start_s && now <= i.stretch.end_s}
              onClick={() => onSeekFrame(i.stretch.start_frame)}
            />
          ),
        )}
      </ol>
      <p className="text-[11px] text-muted-foreground">{view.note}</p>
    </>
  )
}

function EventRow({
  e,
  active,
  onClick,
}: {
  e: StrokeEvent
  active: boolean
  onClick: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex w-full items-start gap-3 p-2 text-left hover:bg-muted/60',
          active && 'bg-primary/5',
        )}
      >
        <span className="w-16 shrink-0 font-mono text-xs tabular-nums">
          {formatClock(eventTime(e), 2)}
        </span>
        <span className="flex flex-col gap-1">
          <Badge variant={STATE_BADGE[e.state]} className="self-start">
            {e.label}
          </Badge>
          {e.time_s == null && (
            <span className="text-xs text-muted-foreground">
              Momento esatto non determinato: finestra {formatClock(e.window_start_s, 2)}–
              {formatClock(e.window_end_s, 2)}
            </span>
          )}
          {e.state !== 'flagged' && e.reasons.length > 0 && (
            <span className="flex flex-col text-xs text-muted-foreground" data-testid="reasons">
              {e.reasons.map((r) => (
                <span key={r}>{r}</span>
              ))}
            </span>
          )}
          <RegionStrip regions={e.regions} />
        </span>
      </button>
    </li>
  )
}

function StretchRow({
  s,
  active,
  onClick,
}: {
  s: NotAssessableStretch
  active: boolean
  onClick: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex w-full items-start gap-3 p-2 text-left hover:bg-muted/60',
          active && 'bg-primary/5',
        )}
      >
        <span className="w-16 shrink-0 font-mono text-xs tabular-nums">
          {formatClock(s.start_s, 2)}
        </span>
        <span className="flex flex-col gap-1">
          <Badge variant="neutral" className="self-start">
            {s.label} · fino a {formatClock(s.end_s, 2)}
          </Badge>
          <span className="text-xs text-muted-foreground">{s.reason}</span>
        </span>
      </button>
    </li>
  )
}

const REGION_DOT: Record<RegionVisibilityState, string> = {
  good: 'border-success bg-success',
  low_but_usable:
    'border-warning bg-[linear-gradient(90deg,var(--color-warning)_50%,transparent_50%)]',
  not_observable: 'border-dashed border-muted-foreground bg-transparent',
}

const REGION_SHORT: Record<RegionVisibilityState, string> = {
  good: '',
  low_but_usable: 'bassa',
  not_observable: 'non osservabile',
}

function RegionDot({ state }: { state: RegionVisibilityState }) {
  return (
    <span
      aria-hidden
      className={cn('inline-block size-2.5 shrink-0 rounded-full border-2', REGION_DOT[state])}
    />
  )
}

function RegionLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      <span>Visibilità delle parti del corpo intorno al colpo (separata dallo stato):</span>
      {(['good', 'low_but_usable', 'not_observable'] as const).map((st) => (
        <span key={st} className="flex items-center gap-1">
          <RegionDot state={st} />
          {st === 'good'
            ? 'buona'
            : st === 'low_but_usable'
              ? 'bassa ma usabile'
              : 'non osservabile'}
        </span>
      ))}
      <span>corsivo = parte non usata dal colpo</span>
    </div>
  )
}

/** Four regions of one event, compact; state and reasons of the event stay apart. */
function RegionStrip({ regions }: { regions: RegionVisibility[] }) {
  if (regions.length === 0) return null
  return (
    <span
      className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground"
      aria-label="Visibilità delle parti del corpo"
      data-testid="regions"
    >
      <span className="font-medium">Visibilità:</span>
      {regions.map((r) => {
        const text = `${r.label}: ${r.state_label}${r.used ? '' : ' (non usata dal colpo)'}${
          r.reasons.length ? ` (${r.reasons.join(', ')})` : ''
        }`
        return (
          <span
            key={r.region}
            title={text}
            aria-label={text}
            data-region={r.region}
            data-region-state={r.state}
            className={cn('flex items-center gap-1', !r.used && 'italic opacity-70')}
          >
            <RegionDot state={r.state} />
            {r.label}
            {REGION_SHORT[r.state] && (
              <span className={r.state === 'not_observable' ? 'text-foreground' : undefined}>
                · {REGION_SHORT[r.state]}
              </span>
            )}
          </span>
        )
      })}
    </span>
  )
}
