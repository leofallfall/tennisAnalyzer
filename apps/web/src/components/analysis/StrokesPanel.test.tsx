import type {
  Analysis,
  AnalysisVideo,
  RegionVisibility,
  RegionVisibilityState,
  ReviewEntry,
  StrokesView,
} from '@tpa/contracts'
import { render, screen, within } from '@testing-library/react'
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

const LABELS = {
  good: 'buona',
  low_but_usable: 'bassa ma usabile',
  not_observable: 'non osservabile',
} as const

const regions = (
  states: [
    RegionVisibilityState,
    RegionVisibilityState,
    RegionVisibilityState,
    RegionVisibilityState,
  ],
  used = [true, true, true, false],
): RegionVisibility[] =>
  (
    [
      ['torso', 'busto'],
      ['left_wrist', 'polso sinistro'],
      ['right_wrist', 'polso destro'],
      ['legs', 'gambe'],
    ] as const
  ).map(([region, label], i) => ({
    region,
    label,
    state: states[i]!,
    state_label: LABELS[states[i]!],
    used: used[i]!,
    reasons: states[i] === 'good' ? [] : ['punti del corpo visti male'],
  }))

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
      regions: regions(['good', 'good', 'good', 'good']),
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
      regions: regions(['good', 'low_but_usable', 'not_observable', 'good']),
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
      regions: regions(['good', 'good', 'good', 'good']),
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
  frame_count: 300,
  my_strokes: [],
  reviewed_stretches: [],
}

const confirmed: ReviewEntry = {
  id: 'r1',
  kind: 'stroke',
  action: 'confirmed',
  action_label: 'Confermato da te',
  frame: 30,
  time_s: 1,
  stroke_type: 'backhand',
  stroke_type_label: 'Rovescio',
  visibility: null,
  visibility_label: null,
  reason: null,
  reason_label: null,
  note: '',
  event_id: 'e0001',
  event_frame: 30,
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

  it('shows the four regions per stroke, apart from state and reasons', async () => {
    mockFetch({ [URL_]: view })
    render(
      <StrokesPanel
        analysis={analysis}
        av={av(URL_)}
        frame={0}
        duration={10}
        onSeekFrame={() => undefined}
        onDetected={() => undefined}
      />,
    )
    const strips = await screen.findAllByTestId('regions')
    expect(strips).toHaveLength(2) // discarded hidden
    const toCheck = strips[1]!
    const states = Array.from(toCheck.querySelectorAll('[data-region]')).map((el) => [
      el.getAttribute('data-region'),
      el.getAttribute('data-region-state'),
    ])
    expect(states).toEqual([
      ['torso', 'good'],
      ['left_wrist', 'low_but_usable'],
      ['right_wrist', 'not_observable'],
      ['legs', 'good'],
    ])
    expect(
      within(toCheck).getByLabelText('polso destro: non osservabile (punti del corpo visti male)'),
    ).toBeInTheDocument()
    expect(within(toCheck).getByLabelText('gambe: buona (non usata dal colpo)')).toBeInTheDocument()
    // the event reasons are a separate block, not inside the regions strip
    const reasons = screen.getByTestId('reasons')
    expect(reasons).toHaveTextContent('Polsi visti male durante il movimento')
    expect(within(toCheck).queryByText(/Polsi visti male/)).not.toBeInTheDocument()
    // legend
    expect(
      screen.getByText(/Visibilità delle parti del corpo intorno al colpo/),
    ).toBeInTheDocument()
    expect(screen.getByText('bassa ma usabile')).toBeInTheDocument()
  })

  it('confirms an event with its type and shows the saved correction', async () => {
    const after: StrokesView = {
      ...view,
      events: view.events.map((e) => (e.id === 'e0001' ? { ...e, review: confirmed } : e)),
    }
    const fetchMock = mockFetch({ [URL_]: view, [`${URL_}/review/entries`]: after })
    render(
      <StrokesPanel
        analysis={analysis}
        av={av(URL_)}
        frame={0}
        duration={10}
        onSeekFrame={() => undefined}
        onDetected={() => undefined}
      />,
    )
    await screen.findByTestId('strokes-summary')
    const first = screen.getAllByTestId('review')[0]!
    await userEvent.click(within(first).getByRole('button', { name: 'Rovescio' }))
    expect(fetchMock).toHaveBeenCalledWith(
      `${URL_}/review/entries`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ event_id: 'e0001', kind: 'stroke', stroke_type: 'backhand' }),
      }),
    )
    expect(await screen.findByText('Confermato da te')).toBeInTheDocument()
    expect(screen.getByTestId('review-summary')).toHaveTextContent('1 confermati')
    // the detector state stays visible next to the correction
    const row = screen.getAllByTestId('review')[0]!.closest('li')!
    expect(within(row).getByText('Colpo segnalato')).toBeInTheDocument()
  })

  it('deletes with a reason, adds a stroke and marks a reviewed stretch at the video frame', async () => {
    const fetchMock = mockFetch({
      [URL_]: view,
      [`${URL_}/review/entries`]: view,
      [`${URL_}/review/stretches`]: view,
    })
    const { rerender } = render(
      <StrokesPanel
        analysis={analysis}
        av={av(URL_)}
        frame={45}
        duration={10}
        onSeekFrame={() => undefined}
        onDetected={() => undefined}
      />,
    )
    await screen.findByTestId('strokes-summary')
    const second = screen.getAllByTestId('review')[1]!
    await userEvent.click(within(second).getByRole('button', { name: 'Elimina…' }))
    await userEvent.click(within(second).getByRole('button', { name: 'Doppione' }))
    expect(fetchMock).toHaveBeenLastCalledWith(
      `${URL_}/review/entries`,
      expect.objectContaining({
        body: JSON.stringify({ event_id: 'e0002', kind: 'not_stroke', reason: 'duplicate' }),
      }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Aggiungi colpo qui' }))
    expect(fetchMock).toHaveBeenLastCalledWith(
      `${URL_}/review/entries`,
      expect.objectContaining({ body: JSON.stringify({ frame: 45, kind: 'stroke' }) }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Inizio tratto rivisto qui' }))
    rerender(
      <StrokesPanel
        analysis={analysis}
        av={av(URL_)}
        frame={120}
        duration={10}
        onSeekFrame={() => undefined}
        onDetected={() => undefined}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /Fine tratto qui/ }))
    expect(fetchMock).toHaveBeenLastCalledWith(
      `${URL_}/review/stretches`,
      expect.objectContaining({ body: JSON.stringify({ start_frame: 45, end_frame: 120 }) }),
    )
  })

  it('hides events deleted by the user unless discarded are shown', () => {
    const deleted: ReviewEntry = {
      ...confirmed,
      kind: 'not_stroke',
      action: 'deleted',
      stroke_type: null,
      stroke_type_label: null,
      reason: 'duplicate',
      reason_label: 'Doppione di un altro colpo',
    }
    const v: StrokesView = {
      ...view,
      events: view.events.map((e) =>
        e.id === 'e0001'
          ? { ...e, review: deleted }
          : e.id === 'e0003'
            ? { ...e, review: { ...confirmed, frame: 150, time_s: 5 } }
            : e,
      ),
    }
    // e0001 deleted by the user: hidden; e0003 discarded by the detector but confirmed: shown
    expect(timelineItems(v, false).map((i) => i.t)).toEqual([3, 5, 6])
    expect(timelineItems(v, true).map((i) => i.t)).toEqual([1, 3, 5, 6])
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
