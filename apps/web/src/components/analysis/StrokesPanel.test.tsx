import type { Analysis, AnalysisVideo, StrokesView } from '@tpa/contracts'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StrokesPanel } from '@/components/analysis/StrokesPanel'
import { timelineItems } from '@/lib/strokes'
import { mockFetch } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const URL_ = '/api/analyses/a1/videos/v1/strokes'

const analysis: Analysis = {
  id: 'a1',
  mode: 'session',
  title: 'Session',
  video_relation: 'single',
  stroke_filter: 'both',
  focus: null,
  dominant_hand: null,
  backhand: null,
  status: 'completed',
  stage: 'completed',
  progress: 1,
  error: null,
  started_at: null,
  finished_at: null,
  updated_at: '2026-01-01T10:00:00Z',
  created_at: '2026-01-01T10:00:00Z',
  videos: [],
  results: null,
}

const av = (strokes_url: string | null): AnalysisVideo => ({
  video_id: 'v1',
  position: 0,
  camera_view: 'unknown',
  original_filename: 'clip.mp4',
  file_url: '/api/videos/v1/file',
  artifacts: {},
  strokes_url,
  stroke_detection: null,
})

// Shape test data for the component only; not analysis output.
const view: StrokesView = {
  analysis_id: 'a1',
  video_id: 'v1',
  algorithm: 'tpa.stroke_detector',
  algorithm_version: 'test',
  experimental: true,
  fps: 30,
  provisional_params: { t_reject: 0.45, t_accept: 0.7 },
  note: "'Non valutabile' = evidenze insufficienti per rilevare colpi, non assenza di colpi.",
  events: [
    {
      id: 'e0001',
      state: 'flagged',
      status: 'accepted',
      label: 'Colpo segnalato',
      time_s: 1,
      frame: 30,
      window_start_s: 0.9,
      window_end_s: 1.1,
      reasons: [],
      reason_codes: [],
    },
    {
      id: 'e0002',
      state: 'to_check',
      status: 'uncertain',
      label: 'Da verificare',
      time_s: null,
      frame: null,
      window_start_s: 3,
      window_end_s: 3.2,
      reasons: ['Polsi visti male durante il movimento'],
      reason_codes: ['uncertain_no_mostly_good_peak'],
    },
    {
      id: 'e0003',
      state: 'discarded',
      status: 'rejected',
      label: 'Scartato',
      time_s: 5,
      frame: 150,
      window_start_s: 5,
      window_end_s: 5,
      reasons: ['Movimento del polso molto più lento'],
      reason_codes: ['reject_low_relative_height'],
    },
  ],
  not_assessable: [
    {
      state: 'not_assessable',
      label: 'Non valutabile',
      start_s: 6,
      end_s: 8,
      start_frame: 180,
      end_frame: 240,
      regions: ['torso'],
      reason: 'Busto senza dati utilizzabili: qui i colpi non si possono rilevare',
    },
  ],
}

describe('timelineItems', () => {
  it('hides discarded events unless asked and orders by time', () => {
    expect(timelineItems(view, false).map((i) => i.t)).toEqual([1, 3, 6])
    expect(timelineItems(view, true).map((i) => i.t)).toEqual([1, 3, 5, 6])
  })
})

describe('StrokesPanel', () => {
  it('shows states, reasons and seeks to the marker frame', async () => {
    mockFetch({ [URL_]: view })
    const onSeek = vi.fn()
    render(
      <StrokesPanel
        analysis={analysis}
        av={av(URL_)}
        frame={0}
        duration={10}
        onSeekFrame={onSeek}
        onDetected={() => undefined}
      />,
    )
    expect(await screen.findByTestId('strokes-summary')).toHaveTextContent(
      '1 colpi segnalati · 1 da verificare · 1 tratti non valutabili · 1 scartati (nascosti)',
    )
    expect(screen.getByText('Polsi visti male durante il movimento')).toBeInTheDocument()
    expect(screen.getByText(/Momento esatto non determinato/)).toBeInTheDocument()
    expect(screen.getAllByText(/non assenza di colpi/).length).toBeGreaterThan(0)
    expect(screen.queryByText('Scartato')).not.toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Colpo segnalato a 0:01.00' }))
    expect(onSeek).toHaveBeenLastCalledWith(30)
    await userEvent.click(screen.getByRole('button', { name: 'Da verificare a 0:03.00' }))
    expect(onSeek).toHaveBeenLastCalledWith(90) // window start when the time is undetermined
    await userEvent.click(screen.getByRole('button', { name: /Non valutabile da 0:06.0/ }))
    expect(onSeek).toHaveBeenLastCalledWith(180)

    await userEvent.click(screen.getByLabelText('Mostra scartati'))
    expect(screen.getByText('Scartato')).toBeInTheDocument()
  })

  it('offers detection on an old analysis without strokes', async () => {
    const fetchMock = mockFetch({})
    const onDetected = vi.fn()
    render(
      <StrokesPanel
        analysis={analysis}
        av={av(null)}
        frame={0}
        duration={10}
        onSeekFrame={() => undefined}
        onDetected={onDetected}
      />,
    )
    expect(screen.getByText(/Colpi non ancora rilevati/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Rileva colpi' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/analyses/a1/strokes', { method: 'POST' })
  })
})
