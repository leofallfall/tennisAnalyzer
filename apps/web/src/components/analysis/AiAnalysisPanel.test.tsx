import type { AiAnalysis, AiAnalysisResult, AiImport, Analysis } from '@tpa/contracts'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AiAnalysisPanel } from '@/components/analysis/AiAnalysisPanel'
import { mockFetch } from '@/test/utils'

afterEach(() => vi.unstubAllGlobals())

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

const awaiting: AiAnalysis = {
  id: 'ai1',
  analysis_id: 'a1',
  kind: 'session',
  pack_id: 'session-1',
  pack_path: 'storage/ai/a1/session-1',
  adapter: 'claude_code',
  status: 'awaiting_result',
  pack_status: 'verified',
  target: null,
  data_status: { strokes: 'not_implemented', measurements: 'not_implemented' },
  result: null,
  model: null,
  validation_errors: [],
  validation_warnings: [],
  import_attempts: 0,
  created_at: '2026-01-01T10:00:00Z',
  imported_at: null,
  last_rejected_at: null,
  imports: [],
  handoff: {
    adapter: 'claude_code',
    interactive: true,
    pack_path: 'storage/ai/a1/session-1',
    command: '/analizza-sessione storage/ai/a1/session-1',
    result_path: 'storage/ai/a1/session-1/analysis_result.json',
    steps: ['Start Claude Code', 'Run the command'],
  },
  images: [],
}

const importedResult: AiAnalysisResult = {
  schema_version: '1.0',
  kind: 'session',
  session_id: 'a1',
  pack_id: 'session-1',
  analysis_version: 'ai-instructions/1.0',
  adapter: 'claude_code',
  model: 'test-model',
  generated_at: '2026-01-01T10:00:00Z',
  language: 'it',
  summary: 'Sintesi reale importata',
  evaluated_strokes: { detected: 2, evaluable: 2, forehand: 2, backhand: 0, unknown: 0 },
  by_stroke_type: [],
  issues: [
    {
      id: 'iss_1',
      priority: 1,
      name: 'Braccio piegato',
      description: 'desc',
      stroke_type: 'forehand',
      nature: 'uncertain',
      occurrences: 1,
      evaluable_strokes: 2,
      percentage: 50,
      relevance: 'medium',
      confidence: 0.5,
      stroke_ids: ['stk_2'],
      timestamps_s: [0.9],
      observations: ['Gomito 120.5°'],
      evidence: [{ kind: 'measurement', ref: 'm_2' }],
      biomechanical_explanation: 'Ipotesi',
      alternative_explanations: [],
      recommended_exercises: [],
    },
  ],
  positive_executions: [],
  recommendations: [],
  limitations: ['Dati sintetici'],
  confidence: 0.5,
  insufficient_evidence: false,
  target: null,
  phases: [],
}

const firstImport: AiImport = {
  id: 'imp1',
  version: 1,
  imported_at: '2026-01-01T11:00:00Z',
  origin: 'import',
  adapter: 'claude_code',
  model: 'test-model',
  pack_id: 'session-1',
  result_sha256: 'a'.repeat(64),
  result: importedResult,
  validation_warnings: [],
}

const imported: AiAnalysis = {
  ...awaiting,
  id: 'ai2',
  status: 'imported',
  handoff: null,
  model: 'test-model',
  result: importedResult,
  imports: [firstImport],
}

function renderPanel(a: Analysis = analysis) {
  return render(<AiAnalysisPanel analysis={a} currentTime={2} duration={10} videoId="v1" />)
}

describe('AI analysis panel', () => {
  it('waits for video processing before offering AI analysis', () => {
    const fetch = mockFetch({})
    renderPanel({ ...analysis, status: 'running' })
    expect(screen.getByText(/Available once video processing has completed/)).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('guides the user through Claude Code and imports the result', async () => {
    const fetch = mockFetch({
      '/api/analyses/a1/ai': [awaiting],
      '/api/ai-analyses/ai1/import': imported,
    })
    renderPanel()
    expect(
      await screen.findByText('/analizza-sessione storage/ai/a1/session-1'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Not in this pack: Strokes \(Not implemented\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Import AI analysis/ }))
    const calls = fetch.mock.calls.map((c) => String(c[0]))
    expect(calls).toContain('/api/ai-analyses/ai1/import')
  })

  it('shows validation errors instead of importing an invalid result', async () => {
    const invalid: AiAnalysis = {
      ...awaiting,
      status: 'invalid',
      validation_errors: [
        { code: 'unknown_stroke', message: "Stroke 'x' is not in the pack", location: 'issues[0]' },
      ],
    }
    mockFetch({ '/api/analyses/a1/ai': [invalid] })
    renderPanel()
    expect(await screen.findByText(/The result was not imported/)).toBeInTheDocument()
    expect(screen.getByText('unknown_stroke')).toBeInTheDocument()
  })

  it('renders an imported analysis and lets the user deep-dive an issue', async () => {
    const fetch = mockFetch({
      '/api/analyses/a1/ai': [imported],
      '/api/analyses/a1/ai/single-stroke': { ...awaiting, id: 'ai3', kind: 'single_stroke' },
    })
    renderPanel()
    expect(await screen.findByText('Sintesi reale importata')).toBeInTheDocument()
    expect(screen.getByText('1/2 evaluable (50%)')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Deep-dive this stroke/ }))
    const calls = fetch.mock.calls as unknown as [string, RequestInit | undefined][]
    const call = calls.find((c) => String(c[0]).endsWith('/ai/single-stroke'))
    expect(call).toBeDefined()
    expect(JSON.parse(String(call![1]!.body))).toEqual({
      stroke_id: 'stk_2',
      source_ai_analysis_id: 'ai2',
      source_import_id: 'imp1',
      issue_id: 'iss_1',
    })
  })

  it('keeps every imported version and shows them in order', async () => {
    const v2: AiAnalysisResult = { ...importedResult, summary: 'Seconda versione', issues: [] }
    const twoVersions: AiAnalysis = {
      ...imported,
      result: v2,
      imports: [
        firstImport,
        { ...firstImport, id: 'imp2', version: 2, result: v2, model: 'other-model' },
      ],
      validation_errors: [{ code: 'invalid_json', message: 'Not valid JSON', location: null }],
      last_rejected_at: '2026-01-01T12:00:00Z',
    }
    mockFetch({ '/api/analyses/a1/ai': [twoVersions] })
    renderPanel()
    expect(await screen.findByText('Seconda versione')).toBeInTheDocument()
    const versions = screen.getAllByRole('button', { name: /^v\d/ })
    expect(versions.map((b) => b.textContent)).toEqual([
      expect.stringMatching(/^v1 · /),
      expect.stringMatching(/^v2 \(latest\) · /),
    ])
    expect(screen.getByText(/other-model \(self-reported\)/)).toBeInTheDocument()
    expect(screen.getByText(/last import attempt was rejected/)).toBeInTheDocument()
    expect(screen.getByText(/Imported versions are unchanged/)).toBeInTheDocument()
    await userEvent.click(versions[0]!)
    expect(screen.getByText('Sintesi reale importata')).toBeInTheDocument()
    expect(screen.queryByText('Seconda versione')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Import new version/ })).toBeInTheDocument()
  })

  it('explains that a legacy pack must be prepared again', async () => {
    mockFetch({
      '/api/analyses/a1/ai': [{ ...imported, pack_status: 'legacy', images: [] }],
    })
    renderPanel()
    expect(await screen.findByText(/created before integrity protection/)).toBeInTheDocument()
    expect(screen.getByText('Sintesi reale importata')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Import new version/ })).not.toBeInTheDocument()
  })
})
