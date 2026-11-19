export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

/** 75.4 -> "1:15.400" ; used for the video clock. */
export function formatClock(seconds: number | null | undefined, decimals = 3): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, seconds)
  const m = Math.floor(s / 60)
  const rest = (s - m * 60).toFixed(decimals).padStart(decimals > 0 ? 3 + decimals : 2, '0')
  return `${m}:${rest}`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const m = Math.floor(seconds / 60)
  return `${m} min ${Math.round(seconds - m * 60)} s`
}

export function formatPercent(fraction: number | null | undefined, digits = 0): string {
  if (fraction == null) return '—'
  return `${(fraction * 100).toFixed(digits)}%`
}

export function formatFps(fps: number | null | undefined): string {
  if (fps == null) return '—'
  return `${Number.isInteger(fps) ? fps : fps.toFixed(2)} fps`
}

/** Timestamps from the API are UTC. A string without an offset (written by an older API
 * from SQLite) is read as UTC too, never as browser-local time. */
export function parseApiDate(iso: string): Date {
  const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso)
  return new Date(hasOffset ? iso : `${iso}Z`)
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return parseApiDate(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function humanize(code: string): string {
  const s = code.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}
