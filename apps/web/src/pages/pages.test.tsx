import type { Analysis } from '@tpa/contracts'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { mockFetch, renderAt } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

const baseAnalysis: Analysis = {
  id: 'a1',
  mode: 'session',
  title: 'Tuesday rally',
  video_relation: 'single',
  stroke_filter: 'both',
  focus: null,
  status: 'created',
  stage: null,
  progress: 0,
  error: null,
  started_at: null,
  finished_at: null,
  updated_at: '2026-01-01T10:00:00Z',
  created_at: '2026-01-01T10:00:00Z',
  videos: [
    {
      video_id: 'v1',
      position: 0,
      camera_view: 'unknown',
      original_filename: 'rally.mp4',
      file_url: '/api/videos/v1/file',
      artifacts: {},
    },
  ],
  results: null,
}

describe('navigation', () => {
  it('shows the six primary sections', async () => {
    mockFetch({ '/api/analyses': [] })
    renderAt('/')
    const nav = screen.getAllByRole('navigation', { name: 'Main' })[0]!
    for (const label of [
      'Dashboard',
      'My Analyses',
      'Progress',
      'Comparisons',
      'Improvement Paths',
      'Player Profile',
    ]) {
      expect(within(nav).getByText(label)).toBeInTheDocument()
    }
    expect(await screen.findByText('No analyses yet')).toBeInTheDocument()
  })
})

describe('new analysis', () => {
  it('offers session and single stroke modes', async () => {
    renderAt('/analyses/new')
    expect(screen.getByText('Session Analysis')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Single Stroke Analysis'))
    expect(screen.getByLabelText('Choose video file')).toHaveAttribute('multiple')
  })

  it('requires the relation between several videos to be declared', async () => {
    renderAt('/analyses/new')
    await userEvent.click(screen.getByText('Single Stroke Analysis'))
    const input = screen.getByLabelText('Choose video file')
    await userEvent.upload(input, [
      new File(['a'], 'side.mp4', { type: 'video/mp4' }),
      new File(['b'], 'behind.mp4', { type: 'video/mp4' }),
    ])
    const submit = screen.getByRole('button', { name: 'Upload and start processing' })
    expect(submit).toBeDisabled()
    await userEvent.click(
      screen.getByLabelText('The same stroke filmed from different camera angles'),
    )
    expect(submit).toBeEnabled()
  })
})

describe('analysis detail', () => {
  it('labels placeholder session data as an example, never as results', async () => {
    mockFetch({
      '/api/analyses/a1': baseAnalysis,
      '/api/videos/v1': {
        id: 'v1',
        original_filename: 'rally.mp4',
        content_type: 'video/mp4',
        size_bytes: 10,
        sha256: 'x',
        created_at: '2026-01-01T10:00:00Z',
        file_url: '/api/videos/v1/file',
        validation: { is_valid: true, issues: [] },
        metadata: {
          path: 'x',
          container: 'mp4',
          file_size_bytes: 10,
          duration_s: 4,
          codec: 'h264',
          pixel_format: 'yuv420p',
          coded_width: 1920,
          coded_height: 1080,
          rotation_deg: 0,
          display_width: 1920,
          display_height: 1080,
          nominal_fps: 60,
          average_fps: 60,
          is_variable_frame_rate: false,
          frame_count: 240,
          bit_rate: null,
          has_audio: false,
        },
      },
    })
    renderAt('/analyses/a1')
    expect(await screen.findByRole('heading', { name: 'Tuesday rally' })).toBeInTheDocument()
    expect(screen.getByText(/Example layout — not a real analysis/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start processing' })).toBeInTheDocument()
    expect(await screen.findByText('1920×1080')).toBeInTheDocument()
  })

  it('shows structured processing errors', async () => {
    mockFetch({
      '/api/analyses/a1': {
        ...baseAnalysis,
        status: 'failed',
        error: {
          code: 'model_not_available',
          message: 'MediaPipe pose model not found',
          details: {},
        },
      },
    })
    renderAt('/analyses/a1')
    expect(await screen.findByText('MediaPipe pose model not found')).toBeInTheDocument()
    expect(screen.getByText('code: model_not_available')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry processing' })).toBeInTheDocument()
  })
})

const systemInfo = {
  api_version: '0.1.0',
  engine_version: '0.1.0',
  python_version: '3.12.3',
  platform: 'Linux',
  database: 'sqlite',
  storage_writable: true,
  tools: [{ name: 'ffmpeg', available: true, version: 'ffmpeg 6.1' }],
  pose_model: { name: 'pose_landmarker_full.task', available: true, version: null },
  pose_runtime: {
    name: 'mediapipe_runtime',
    available: false,
    version: null,
    detail: 'The MediaPipe runtime could not be loaded (OSError). Install libegl1 libgles2.',
  },
  capabilities: [],
}

describe('system status', () => {
  it('reports a MediaPipe runtime that cannot load, with the fix', async () => {
    mockFetch({ '/api/system/info': systemInfo, '/api/analyses': [] })
    renderAt('/system')
    expect(await screen.findByText(/Pose estimation cannot run/)).toHaveTextContent(
      'libegl1 libgles2',
    )
    expect(screen.getByText('mediapipe_runtime')).toBeInTheDocument()
    expect(screen.getByText('Writable')).toBeInTheDocument()
  })
})
