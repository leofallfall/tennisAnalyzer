import { describe, expect, it } from 'vitest'

import {
  formatBytes,
  formatClock,
  formatFps,
  formatPercent,
  humanize,
  parseApiDate,
} from './format'

describe('format helpers', () => {
  it('formats bytes', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(null)).toBe('—')
  })
  it('formats clock with milliseconds', () => {
    expect(formatClock(75.4)).toBe('1:15.400')
    expect(formatClock(5.25)).toBe('0:05.250')
  })
  it('formats fps and percent', () => {
    expect(formatFps(30)).toBe('30 fps')
    expect(formatFps(29.97)).toBe('29.97 fps')
    expect(formatPercent(0.543)).toBe('54%')
  })
  it('humanizes codes', () => {
    expect(humanize('partially_observable')).toBe('Partially observable')
  })
  it('reads API timestamps as UTC, with or without an offset', () => {
    const expected = '2026-11-19T11:02:45.559Z'
    expect(parseApiDate('2026-11-19T11:02:45.559541').toISOString()).toBe(expected)
    expect(parseApiDate('2026-11-19T11:02:45.559541Z').toISOString()).toBe(expected)
    expect(parseApiDate('2026-11-19T12:02:45.559541+01:00').toISOString()).toBe(expected)
  })
})
