import type { NotAssessableStretch, ReviewEntry, StrokeEvent, StrokesView } from '@tpa/contracts'

import { frameAtTime } from '@/lib/frames'

/** Frame a marker jumps to: the user's frame when corrected as a stroke, else the
 * reference frame, or the window start when the wrist is undetermined. */
export function eventFrame(e: StrokeEvent, fps: number): number {
  if (e.review?.kind === 'stroke') return e.review.frame
  return e.frame ?? frameAtTime(e.window_start_s, fps)
}

export function eventTime(e: StrokeEvent): number {
  if (e.review?.kind === 'stroke') return e.review.time_s
  return e.time_s ?? e.window_start_s
}

/** Shown when discarded events are hidden: not discarded by the detector (unless the user
 * confirmed it as a stroke) and not deleted by the user. */
export function isVisible(e: StrokeEvent, showDiscarded: boolean): boolean {
  if (showDiscarded) return true
  if (e.review) return e.review.kind === 'stroke'
  return e.state !== 'discarded'
}

export type TimelineItem =
  | { kind: 'event'; t: number; event: StrokeEvent }
  | { kind: 'stretch'; t: number; stretch: NotAssessableStretch }
  | { kind: 'mine'; t: number; entry: ReviewEntry }

/** Time-ordered list; discarded events only when asked for (hidden by default, ADR-025). */
export function timelineItems(view: StrokesView, showDiscarded: boolean): TimelineItem[] {
  const items: TimelineItem[] = [
    ...view.events
      .filter((e) => isVisible(e, showDiscarded))
      .map((e) => ({ kind: 'event' as const, t: eventTime(e), event: e })),
    ...view.not_assessable.map((s) => ({ kind: 'stretch' as const, t: s.start_s, stretch: s })),
    ...(view.my_strokes ?? [])
      .filter((m) => showDiscarded || m.kind === 'stroke')
      .map((m) => ({ kind: 'mine' as const, t: m.time_s, entry: m })),
  ]
  return items.sort((a, b) => a.t - b.t)
}

export interface ReviewCounts {
  confirmed: number
  moved: number
  deleted: number
  added: number
  detached: number
  reviewedFrames: number
}

export function reviewCounts(view: StrokesView): ReviewCounts {
  const c: ReviewCounts = {
    confirmed: 0,
    moved: 0,
    deleted: 0,
    added: 0,
    detached: 0,
    reviewedFrames: 0,
  }
  for (const e of view.events) if (e.review) c[e.review.action] += 1
  for (const m of view.my_strokes ?? []) c[m.action] += 1
  for (const s of view.reviewed_stretches ?? []) c.reviewedFrames += s.end_frame - s.start_frame + 1
  return c
}

/** Visible window of the timeline: the whole video, or ``span`` seconds around ``now``. */
export function timelineWindow(
  end: number,
  now: number,
  span: number | null,
): { lo: number; hi: number } {
  if (span == null || span >= end) return { lo: 0, hi: end }
  const lo = Math.min(Math.max(0, now - span / 2), end - span)
  return { lo, hi: lo + span }
}
