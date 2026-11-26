import type {
  Analysis,
  AnalysisVideo,
  NotAssessableStretch,
  RegionVisibility,
  RegionVisibilityState,
  ReviewEntry,
  StrokeEvent,
  StrokesView,
} from '@tpa/contracts'
import { useState } from 'react'

import { type ReviewActions, ReviewControls } from '@/components/analysis/StrokeReview'

import { ErrorAlert } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'
import { formatClock } from '@/lib/format'
import { useAsync } from '@/lib/hooks'
import {
  eventFrame,
  eventTime,
  isVisible,
  reviewCounts,
  timelineItems,
  timelineWindow,
} from '@/lib/strokes'
import { cn } from '@/lib/utils'

/** Grey stripes: not the amber hatch used for example/placeholder content. */
const NA_STRIPES = {
  backgroundImage:
    'repeating-linear-gradient(135deg, color-mix(in oklch, var(--color-foreground) 16%, transparent) 0 4px, transparent 4px 8px)',
}

/** Seconds shown when the timeline is zoomed (long videos: markers closer than 1 s). */
const ZOOM_SPAN_S = 20

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
  const { data: loaded, error } = useAsync<StrokesView | null>(
    () => (url ? api.getStrokes(url) : Promise.resolve(null)),
    [url],
  )
  // the view returned by the last saved correction (same shape as the GET)
  const [saved, setSaved] = useState<{ url: string; view: StrokesView } | null>(null)
  const view = saved && saved.url === url && loaded ? saved.view : loaded
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [showDiscarded, setShowDiscarded] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [detectError, setDetectError] = useState<string | null>(null)
  const busy = analysis.status === 'queued' || analysis.status === 'running'
  const detection = av.stroke_detection ?? analysis.results?.strokes?.[av.video_id] ?? null

  const save = (p: () => Promise<StrokesView>) => {
    if (!url) return
    setSaving(true)
    setSaveError(null)
    p()
      .then((v) => setSaved({ url, view: v }))
      .catch((e: unknown) => setSaveError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }
  const actions: ReviewActions | null = url
    ? {
        create: (body) => save(() => api.addReviewEntry(url, body)),
        update: (id, body) => save(() => api.updateReviewEntry(url, id, body)),
        remove: (id) => save(() => api.deleteReviewEntry(url, id)),
      }
    : null

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
          quel momento del video. Le tue correzioni sono salvate a parte: il risultato del
          rilevatore non viene modificato e le correzioni restano anche se rilevi di nuovo.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && <ErrorAlert title="Impossibile caricare i colpi" message={error.message} />}
        {detectError && <ErrorAlert title="Impossibile avviare" message={detectError} />}
        {saveError && <ErrorAlert title="Correzione non salvata" message={saveError} />}
        {!url && (
          <NoStrokes
            analysis={analysis}
            busy={busy}
            failed={detection?.status === 'failed' ? (detection.error?.message ?? '') : null}
            detecting={detecting}
            onDetect={detect}
          />
        )}
        {view && url && actions && (
          <StrokesBody
            view={view}
            frame={frame}
            duration={duration}
            showDiscarded={showDiscarded}
            setShowDiscarded={setShowDiscarded}
            onSeekFrame={onSeekFrame}
            actions={actions}
            busy={saving}
            onAddStretch={(a, b) => save(() => api.addReviewedStretch(url, a, b))}
            onDeleteStretch={(id) => save(() => api.deleteReviewedStretch(url, id))}
            exportUrl={api.reviewExportUrl(url)}
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
  actions,
  busy,
  onAddStretch,
  onDeleteStretch,
  exportUrl,
}: {
  view: StrokesView
  frame: number
  duration: number
  showDiscarded: boolean
  setShowDiscarded: (v: boolean) => void
  onSeekFrame: (frame: number) => void
  actions: ReviewActions
  busy: boolean
  onAddStretch: (startFrame: number, endFrame: number) => void
  onDeleteStretch: (id: string) => void
  exportUrl: string
}) {
  const [zoom, setZoom] = useState(false)
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
  const now = frame / fps
  const win = timelineWindow(end, now, zoom ? ZOOM_SPAN_S : null)
  const span = win.hi - win.lo
  const pct = (t: number) => `${Math.min(100, Math.max(0, ((t - win.lo) / span) * 100))}%`
  const width = (a: number, b: number) =>
    `${Math.max(0, ((Math.min(b, win.hi) - Math.max(a, win.lo)) / span) * 100)}%`
  const inWin = (t: number) => t >= win.lo && t <= win.hi
  const overlaps = (a: number, b: number) => b >= win.lo && a <= win.hi
  const items = timelineItems(view, showDiscarded)
  const shownEvents = view.events.filter((e) => isVisible(e, showDiscarded) && inWin(eventTime(e)))
  const shownMine = items.flatMap((i) => (i.kind === 'mine' && inWin(i.t) ? [i.entry] : []))
  const counts = reviewCounts(view)

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
        {view.not_assessable
          .filter((s) => overlaps(s.start_s, s.end_s))
          .map((s) => (
            <button
              type="button"
              key={`na-${s.start_frame}`}
              title={`Non valutabile ${formatClock(s.start_s, 1)}–${formatClock(s.end_s, 1)}: ${s.reason}`}
              aria-label={`Non valutabile da ${formatClock(s.start_s, 1)} a ${formatClock(s.end_s, 1)}`}
              onClick={() => onSeekFrame(s.start_frame)}
              className="absolute inset-y-0 bg-foreground/5"
              style={{ ...NA_STRIPES, left: pct(s.start_s), width: width(s.start_s, s.end_s) }}
            />
          ))}
        {view.reviewed_stretches
          .filter((s) => overlaps(s.start_s, s.end_s))
          .map((s) => (
            <span
              key={`rv-${s.id}`}
              title={`Tratto rivisto ${formatClock(s.start_s, 1)}–${formatClock(s.end_s, 1)}`}
              data-testid="reviewed-band"
              className="pointer-events-none absolute bottom-0 h-1 bg-success"
              style={{ left: pct(s.start_s), width: width(s.start_s, s.end_s) }}
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
              e.review?.kind === 'stroke' && 'ring-2 ring-success ring-offset-1',
              e.review?.kind === 'not_stroke' && 'opacity-40',
            )}
            style={{ left: pct(eventTime(e)) }}
          />
        ))}
        {shownMine.map((m) => (
          <button
            type="button"
            key={`my-${m.id}`}
            title={`${m.action_label} ${formatClock(m.time_s, 2)}`}
            aria-label={`${m.action_label} a ${formatClock(m.time_s, 2)}`}
            onClick={() => onSeekFrame(m.frame)}
            className={cn(
              'absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rotate-45 border-2 border-success bg-success/60 hover:scale-150',
              m.kind === 'not_stroke' && 'opacity-40',
            )}
            style={{ left: pct(m.time_s) }}
          />
        ))}
        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground"
          style={{ left: pct(now) }}
        />
      </div>
      <div className="-mt-1 flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{formatClock(win.lo, 1)}</span>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={zoom} onChange={(e) => setZoom(e.target.checked)} />
          Ingrandisci ({ZOOM_SPAN_S} s intorno al punto del video)
        </label>
        <span>{formatClock(win.hi, 1)}</span>
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
        <span className="flex items-center gap-1">
          <span className="size-2.5 rotate-45 border-2 border-success bg-success/60" /> Aggiunto da
          te
        </span>
        <span className="flex items-center gap-1">
          <span className="h-1 w-4 bg-success" /> Tratto rivisto
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

      <ReviewToolbar
        view={view}
        frame={frame}
        busy={busy}
        counts={counts}
        onAdd={() => actions.create({ frame, kind: 'stroke' })}
        onAddStretch={onAddStretch}
        onDeleteStretch={onDeleteStretch}
        onSeekFrame={onSeekFrame}
        exportUrl={exportUrl}
      />

      <ol className="max-h-[32rem] divide-y overflow-y-auto rounded-md border text-sm">
        {items.length === 0 && (
          <li className="p-3 text-muted-foreground">Nessun colpo da mostrare.</li>
        )}
        {items.map((i) =>
          i.kind === 'event' ? (
            <EventRow
              key={`${i.event.id}-${i.event.review?.id ?? ''}`}
              e={i.event}
              active={now >= i.event.window_start_s && now <= i.event.window_end_s}
              onClick={() => onSeekFrame(eventFrame(i.event, fps))}
              frame={frame}
              busy={busy}
              actions={actions}
            />
          ) : i.kind === 'mine' ? (
            <MyStrokeRow
              key={`my-${i.entry.id}`}
              m={i.entry}
              active={frame === i.entry.frame}
              onClick={() => onSeekFrame(i.entry.frame)}
              frame={frame}
              busy={busy}
              actions={actions}
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
  frame,
  busy,
  actions,
}: {
  e: StrokeEvent
  active: boolean
  onClick: () => void
  frame: number
  busy: boolean
  actions: ReviewActions
}) {
  return (
    <li className={cn('flex flex-col gap-1 p-2', active && 'bg-primary/5')}>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-3 rounded text-left hover:bg-muted/60"
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
      <div className="pl-[4.75rem]">
        <ReviewControls
          eventId={e.id}
          entry={e.review ?? null}
          currentFrame={frame}
          busy={busy}
          actions={actions}
        />
      </div>
    </li>
  )
}

function MyStrokeRow({
  m,
  active,
  onClick,
  frame,
  busy,
  actions,
}: {
  m: ReviewEntry
  active: boolean
  onClick: () => void
  frame: number
  busy: boolean
  actions: ReviewActions
}) {
  return (
    <li className={cn('flex flex-col gap-1 p-2', active && 'bg-primary/5')}>
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-3 rounded text-left hover:bg-muted/60"
      >
        <span className="w-16 shrink-0 font-mono text-xs tabular-nums">
          {formatClock(m.time_s, 2)}
        </span>
        <span className="text-xs text-muted-foreground">
          {m.action === 'detached'
            ? 'Il rilevatore è stato eseguito di nuovo e il colpo su cui avevi lavorato è cambiato: la tua correzione resta qui, con il suo momento.'
            : 'Colpo che il rilevatore non aveva trovato.'}
        </span>
      </button>
      <div className="pl-[4.75rem]">
        <ReviewControls entry={m} currentFrame={frame} busy={busy} actions={actions} />
      </div>
    </li>
  )
}

function ReviewToolbar({
  view,
  frame,
  busy,
  counts,
  onAdd,
  onAddStretch,
  onDeleteStretch,
  onSeekFrame,
  exportUrl,
}: {
  view: StrokesView
  frame: number
  busy: boolean
  counts: ReturnType<typeof reviewCounts>
  onAdd: () => void
  onAddStretch: (startFrame: number, endFrame: number) => void
  onDeleteStretch: (id: string) => void
  onSeekFrame: (frame: number) => void
  exportUrl: string
}) {
  const [start, setStart] = useState<number | null>(null)
  const fps = view.fps
  const total = Math.max(1, view.frame_count)
  return (
    <div className="flex flex-col gap-2 rounded-md border bg-muted/30 p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Correzioni</span>
        <span className="font-mono tabular-nums text-muted-foreground">
          video a {formatClock(frame / fps, 2)} (fotogramma #{frame})
        </span>
        <Button size="sm" variant="outline" className="h-7" disabled={busy} onClick={onAdd}>
          Aggiungi colpo qui
        </Button>
        {start == null ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={busy}
            onClick={() => setStart(frame)}
          >
            Inizio tratto rivisto qui
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              className="h-7"
              disabled={busy}
              onClick={() => {
                onAddStretch(start, frame)
                setStart(null)
              }}
            >
              Fine tratto qui (da {formatClock(start / fps, 1)})
            </Button>
            <Button size="sm" variant="ghost" className="h-7" onClick={() => setStart(null)}>
              Annulla tratto
            </Button>
          </>
        )}
        <Button asChild size="sm" variant="ghost" className="ml-auto h-7">
          <a href={exportUrl} download>
            Esporta come etichette
          </a>
        </Button>
      </div>
      <p className="text-muted-foreground" data-testid="review-summary">
        {counts.confirmed} confermati · {counts.moved} spostati · {counts.deleted} eliminati ·{' '}
        {counts.added} aggiunti
        {counts.detached > 0 && <> · {counts.detached} da ricontrollare (rilevatore cambiato)</>}
        {' · '}tratti rivisti: {view.reviewed_stretches.length} (
        {formatClock(counts.reviewedFrames / fps, 0)} su {formatClock(total / fps, 0)})
      </p>
      {view.reviewed_stretches.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {view.reviewed_stretches.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-1 rounded border border-success/40 bg-success/10 px-1.5 py-0.5"
            >
              <button type="button" onClick={() => onSeekFrame(s.start_frame)}>
                {formatClock(s.start_s, 1)}–{formatClock(s.end_s, 1)}
              </button>
              <button
                type="button"
                aria-label={`Togli tratto rivisto ${formatClock(s.start_s, 1)}`}
                disabled={busy}
                onClick={() => onDeleteStretch(s.id)}
                className="px-0.5 text-muted-foreground hover:text-danger"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] text-muted-foreground">
        Tratto rivisto = l'hai guardato tutto e ogni colpo lì dentro è confermato o aggiunto. Per
        spostare un colpo porta il video sul fotogramma giusto (frecce del player) e premi «Sposta
        qui». Nell'esportazione restano fuori i colpi senza tipo o senza visibilità dell'impatto.
      </p>
    </div>
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
