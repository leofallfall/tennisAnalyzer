import type { NotAssessableStretch, StrokeEvent, StrokesView } from '@tpa/contracts'

import { frameAtTime } from '@/lib/frames'

/** Frame a marker jumps to: the reference frame, or the window start when the wrist is
 * undetermined (no reference time). */
export function eventFrame(e: StrokeEvent, fps: number): number {
  return e.frame ?? frameAtTime(e.window_start_s, fps)
}

export function eventTime(e: StrokeEvent): number {
  return e.time_s ?? e.window_start_s
}

export type TimelineItem =
  | { kind: 'event'; t: number; event: StrokeEvent }
  | { kind: 'stretch'; t: number; stretch: NotAssessableStretch }

/** Time-ordered list; discarded events only when asked for (hidden by default, ADR-025). */
export function timelineItems(view: StrokesView, showDiscarded: boolean): TimelineItem[] {
  const items: TimelineItem[] = [
    ...view.events
      .filter((e) => showDiscarded || e.state !== 'discarded')
      .map((e) => ({ kind: 'event' as const, t: eventTime(e), event: e })),
    ...view.not_assessable.map((s) => ({ kind: 'stretch' as const, t: s.start_s, stretch: s })),
  ]
  return items.sort((a, b) => a.t - b.t)
}
