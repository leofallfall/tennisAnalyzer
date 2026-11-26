import type {
  ImpactVisibility,
  NotStrokeReason,
  ReviewEntry,
  ReviewEntryCreate,
  ReviewEntryUpdate,
  ReviewStrokeType,
} from '@tpa/contracts'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** V1 step 5 (ADR-031): the user's corrections. Texts in Italian, like the strokes panel. */

const TYPE_OPTIONS: { value: ReviewStrokeType; label: string }[] = [
  { value: 'forehand', label: 'Dritto' },
  { value: 'backhand', label: 'Rovescio' },
  { value: 'other', label: 'Altro' },
]

const VISIBILITY_OPTIONS: { value: ImpactVisibility; label: string }[] = [
  { value: 'full', label: 'si vede' },
  { value: 'partial', label: 'in parte' },
  { value: 'none', label: 'non si vede' },
]

const REASON_OPTIONS: { value: NotStrokeReason; label: string }[] = [
  { value: 'duplicate', label: 'Doppione' },
  { value: 'not_a_stroke', label: 'Non è un colpo' },
  { value: 'other_person', label: 'Scheletro su altro' },
  { value: 'out_of_frame', label: 'Fuori inquadratura' },
  { value: 'other', label: 'Altro motivo' },
]

export interface ReviewActions {
  create: (body: ReviewEntryCreate) => void
  update: (id: string, body: ReviewEntryUpdate) => void
  remove: (id: string) => void
}

function toUpdate(e: ReviewEntry): ReviewEntryUpdate {
  return {
    kind: e.kind,
    frame: e.frame,
    stroke_type: e.stroke_type,
    visibility: e.visibility,
    reason: e.reason,
    note: e.note,
  }
}

const ACTION_BADGE = {
  confirmed: 'success',
  moved: 'success',
  added: 'success',
  deleted: 'danger',
  detached: 'info',
} as const

function Choice<T extends string>({
  label,
  options,
  value,
  onPick,
  disabled,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T | null
  onPick: (v: T) => void
  disabled?: boolean
}) {
  return (
    <span className="flex flex-wrap items-center gap-1" role="group" aria-label={label}>
      <span className="text-[11px] text-muted-foreground">{label}:</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          disabled={disabled}
          aria-pressed={value === o.value}
          onClick={() => onPick(o.value)}
          className={cn(
            'rounded border px-1.5 py-0.5 text-[11px] hover:bg-muted disabled:opacity-50',
            value === o.value && 'border-primary bg-primary/10 font-medium text-primary',
          )}
        >
          {o.label}
        </button>
      ))}
    </span>
  )
}

/**
 * Controls under one detector event (``eventId``) or one of the user's own strokes
 * (``entry`` without event). Without an entry, every button creates the correction.
 */
export function ReviewControls({
  eventId,
  entry,
  currentFrame,
  busy,
  actions,
}: {
  eventId?: string
  entry: ReviewEntry | null
  currentFrame: number
  busy: boolean
  actions: ReviewActions
}) {
  const [deleting, setDeleting] = useState(false)
  const [note, setNote] = useState(entry?.note ?? '')
  const isStroke = entry?.kind === 'stroke'

  const saveStroke = (patch: Partial<ReviewEntryUpdate>) => {
    if (entry && entry.kind === 'stroke') {
      actions.update(entry.id, { ...toUpdate(entry), ...patch })
    } else if (entry) {
      // a deleted event made a stroke again
      actions.update(entry.id, {
        ...toUpdate(entry),
        kind: 'stroke',
        reason: null,
        frame: entry.frame,
        ...patch,
      })
    } else {
      actions.create({ event_id: eventId, kind: 'stroke', ...patch })
    }
  }
  const deleteWith = (reason: NotStrokeReason) => {
    setDeleting(false)
    if (entry) {
      actions.update(entry.id, {
        kind: 'not_stroke',
        frame: entry.event_frame ?? entry.frame,
        reason,
        stroke_type: null,
        visibility: null,
        note: entry.note,
      })
    } else actions.create({ event_id: eventId, kind: 'not_stroke', reason })
  }

  return (
    <div className="flex flex-col gap-1.5 border-t border-dashed pt-1.5" data-testid="review">
      {entry && (
        <span className="flex flex-wrap items-center gap-1.5 text-xs">
          <Badge variant={ACTION_BADGE[entry.action]}>{entry.action_label}</Badge>
          {entry.kind === 'stroke' && entry.stroke_type_label && (
            <strong>{entry.stroke_type_label}</strong>
          )}
          {entry.kind === 'not_stroke' && entry.reason_label && <span>{entry.reason_label}</span>}
          {entry.action === 'moved' && entry.event_frame != null && (
            <span className="text-muted-foreground">
              (#{entry.event_frame} → #{entry.frame})
            </span>
          )}
          {entry.kind === 'stroke' && entry.stroke_type == null && (
            <span className="text-muted-foreground">tipo non indicato</span>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px]"
            disabled={busy}
            onClick={() => actions.remove(entry.id)}
          >
            Annulla correzione
          </Button>
        </span>
      )}
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {!entry && eventId && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={busy}
            onClick={() => saveStroke({})}
          >
            Conferma
          </Button>
        )}
        <Choice
          label="Tipo"
          options={TYPE_OPTIONS}
          value={isStroke ? (entry?.stroke_type ?? null) : null}
          disabled={busy}
          onPick={(v) => saveStroke({ stroke_type: v })}
        />
        {isStroke && (
          <Choice
            label="Impatto"
            options={VISIBILITY_OPTIONS}
            value={entry?.visibility ?? null}
            disabled={busy}
            onPick={(v) => saveStroke({ visibility: v })}
          />
        )}
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-[11px]"
          disabled={busy || (isStroke && entry?.frame === currentFrame)}
          title="Sposta il colpo sul fotogramma mostrato dal video"
          onClick={() => saveStroke({ frame: currentFrame })}
        >
          Sposta qui (#{currentFrame})
        </Button>
        {eventId && entry?.kind !== 'not_stroke' && !deleting && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            disabled={busy}
            onClick={() => setDeleting(true)}
          >
            Elimina…
          </Button>
        )}
        {deleting && (
          <span className="flex flex-wrap items-center gap-1" role="group" aria-label="Motivo">
            <span className="text-[11px] text-muted-foreground">Perché non è un colpo:</span>
            {REASON_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                disabled={busy}
                onClick={() => deleteWith(o.value)}
                className="rounded border border-danger/40 px-1.5 py-0.5 text-[11px] text-danger hover:bg-danger/10"
              >
                {o.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setDeleting(false)}
              className="px-1 text-[11px] text-muted-foreground underline"
            >
              lascia stare
            </button>
          </span>
        )}
        {entry && (
          <input
            aria-label="Nota"
            placeholder="Nota (facoltativa)"
            value={note}
            maxLength={500}
            disabled={busy}
            onChange={(ev) => setNote(ev.target.value)}
            onBlur={() => {
              if (note.trim() !== entry.note) actions.update(entry.id, { ...toUpdate(entry), note })
            }}
            className="h-6 min-w-40 flex-1 rounded border border-input bg-card px-2 text-[11px]"
          />
        )}
      </span>
    </div>
  )
}
