import type {
  AiAnalysis,
  AiAnalysisResult,
  AiEvidenceImage,
  AiIssue,
  Analysis,
  EvidenceRef,
  Exercise,
} from '@tpa/contracts'
import { Bot, Check, Copy, Download, Loader2, Sparkles } from 'lucide-react'
import { useState } from 'react'

import { ErrorAlert } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label, Select } from '@/components/ui/input'
import { api } from '@/lib/api'
import { formatClock, formatDateTime, humanize } from '@/lib/format'
import { useAsync } from '@/lib/hooks'

type DeclaredType = 'forehand' | 'backhand' | 'unknown'

const STATUS_LABEL: Record<AiAnalysis['status'], string> = {
  awaiting_result: 'Waiting for Claude Code',
  imported: 'Imported',
  invalid: 'Result rejected',
}

/**
 * Personal AI analysis through Claude Code: prepare an Evidence Pack, run Claude Code
 * interactively, import the validated result. The app never calls an LLM itself.
 */
export function AiAnalysisPanel({
  analysis,
  currentTime,
  duration,
  videoId,
}: {
  analysis: Analysis
  currentTime: number
  duration: number
  videoId: string
}) {
  const ready = analysis.status === 'completed'
  const list = useAsync<AiAnalysis[]>(
    () => (ready ? api.listAiAnalyses(analysis.id) : Promise.resolve([])),
    [analysis.id, ready],
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [declared, setDeclared] = useState<DeclaredType>('unknown')

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setActionError(null)
    try {
      await fn()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
      list.reload()
    }
  }

  const prepareSession = () => run('session', () => api.prepareSessionAi(analysis.id))
  const prepareStroke = () => {
    const isClip = analysis.mode === 'single_stroke'
    const start = Math.max(0, currentTime - 1)
    const end = Math.min(duration || currentTime + 1, currentTime + 1)
    return run('stroke', () =>
      api.prepareSingleStrokeAi(analysis.id, {
        video_id: videoId,
        ...(isClip ? {} : { start_s: round3(start), end_s: round3(end) }),
        ...(declared === 'unknown' ? {} : { declared_stroke_type: declared }),
      }),
    )
  }
  const deepDive = (source: AiAnalysis, issue: AiIssue, strokeId: string) =>
    run(`deep-${issue.id}`, () =>
      api.prepareSingleStrokeAi(analysis.id, {
        stroke_id: strokeId,
        source_ai_analysis_id: source.id,
        issue_id: issue.id,
      }),
    )
  const importResult = (ai: AiAnalysis) => run(`import-${ai.id}`, () => api.importAiResult(ai.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4" /> AI analysis (Claude Code)
        </CardTitle>
        <CardDescription>
          Personal mode: the app prepares the evidence, you run Claude Code with your own
          subscription, then import the result. No API calls, no API key, nothing uploaded.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!ready ? (
          <p className="text-sm text-muted-foreground">
            Available once video processing has completed.
          </p>
        ) : (
          <div className="flex flex-wrap items-end gap-3">
            {analysis.mode === 'session' && (
              <Button onClick={prepareSession} disabled={busy !== null}>
                {busy === 'session' ? <Loader2 className="animate-spin" /> : <Sparkles />}
                Prepare AI analysis
              </Button>
            )}
            <div className="flex flex-col gap-1">
              <Label htmlFor="declared-type" className="text-[11px]">
                Stroke type (your declaration)
              </Label>
              <Select
                id="declared-type"
                value={declared}
                onChange={(e) => setDeclared(e.target.value as DeclaredType)}
              >
                <option value="unknown">Not declared</option>
                <option value="forehand">Forehand</option>
                <option value="backhand">Backhand</option>
              </Select>
            </div>
            <Button
              variant={analysis.mode === 'session' ? 'outline' : 'default'}
              onClick={prepareStroke}
              disabled={busy !== null}
            >
              {busy === 'stroke' ? <Loader2 className="animate-spin" /> : <Sparkles />}
              {analysis.mode === 'session'
                ? `Prepare single-stroke analysis at ${formatClock(currentTime, 1)} (±1 s)`
                : 'Prepare single-stroke AI analysis'}
            </Button>
          </div>
        )}
        {actionError && <ErrorAlert title="Action failed" message={actionError} />}
        {list.error && (
          <ErrorAlert title="Could not load AI analyses" message={list.error.message} />
        )}
        {(list.data ?? []).map((ai) => (
          <AiAnalysisItem
            key={ai.id}
            ai={ai}
            busy={busy}
            onImport={() => importResult(ai)}
            onDeepDive={(issue, strokeId) => deepDive(ai, issue, strokeId)}
          />
        ))}
      </CardContent>
    </Card>
  )
}

function round3(n: number) {
  return Math.round(n * 1000) / 1000
}

function AiAnalysisItem({
  ai,
  busy,
  onImport,
  onDeepDive,
}: {
  ai: AiAnalysis
  busy: string | null
  onImport: () => void
  onDeepDive: (issue: AiIssue, strokeId: string) => void
}) {
  const variant = ai.status === 'imported' ? 'success' : ai.status === 'invalid' ? 'danger' : 'info'
  const title =
    ai.kind === 'session'
      ? 'Session analysis'
      : `Single stroke ${ai.target ? `${formatClock(ai.target.interval.start_s, 1)}–${formatClock(ai.target.interval.end_s, 1)}` : ''}`
  const notAvailable = Object.entries(ai.data_status).filter(([, v]) => v !== 'available')
  return (
    <section
      aria-label={title}
      className="flex flex-col gap-3 rounded-md border bg-card p-3"
      data-testid={`ai-${ai.id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">{title}</p>
        <Badge variant={variant}>{STATUS_LABEL[ai.status]}</Badge>
        {ai.target?.motivating_issue && (
          <Badge variant="primary">Issue: {ai.target.motivating_issue.name}</Badge>
        )}
        <span className="text-xs text-muted-foreground">{formatDateTime(ai.created_at)}</span>
        {ai.model && <span className="text-xs text-muted-foreground">· {ai.model}</span>}
      </div>
      {notAvailable.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Not in this pack:{' '}
          {notAvailable.map(([k, v]) => `${humanize(k)} (${humanize(v)})`).join(', ')}
        </p>
      )}
      {ai.handoff && <Handoff ai={ai} busy={busy} onImport={onImport} />}
      {ai.validation_errors.length > 0 && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm">
          <p className="font-medium text-danger">
            The result was not imported ({ai.validation_errors.length} problem
            {ai.validation_errors.length === 1 ? '' : 's'}). Fix analysis_result.json (or ask Claude
            Code to fix it) and import again.
          </p>
          <ul className="mt-2 list-disc pl-5 text-xs">
            {ai.validation_errors.map((e, i) => (
              <li key={i}>
                <span className="font-mono">{e.code}</span>
                {e.location && <span className="font-mono"> @ {e.location}</span>}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {ai.result && <AiResultView ai={ai} result={ai.result} busy={busy} onDeepDive={onDeepDive} />}
    </section>
  )
}

function Handoff({
  ai,
  busy,
  onImport,
}: {
  ai: AiAnalysis
  busy: string | null
  onImport: () => void
}) {
  const [copied, setCopied] = useState(false)
  const handoff = ai.handoff!
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(handoff.command)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="flex flex-col gap-2 rounded-md bg-muted/60 p-3 text-sm">
      <p className="font-medium">Evidence ready. Next steps:</p>
      <ol className="list-decimal pl-5 text-xs">
        {handoff.steps.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ol>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded border bg-card px-2 py-1 font-mono text-xs">
          {handoff.command}
        </code>
        <Button size="sm" variant="outline" onClick={copy} aria-label="Copy command">
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Pack folder: <span className="font-mono">{handoff.pack_path}</span> · result file:{' '}
        <span className="font-mono">{handoff.result_path}</span>
      </p>
      <Button size="sm" className="self-start" onClick={onImport} disabled={busy !== null}>
        {busy === `import-${ai.id}` ? <Loader2 className="animate-spin" /> : <Download />}
        Import AI analysis
      </Button>
    </div>
  )
}

function pct(n: number) {
  return `${Math.round(n * 100)}%`
}

function AiResultView({
  ai,
  result,
  busy,
  onDeepDive,
}: {
  ai: AiAnalysis
  result: AiAnalysisResult
  busy: string | null
  onDeepDive: (issue: AiIssue, strokeId: string) => void
}) {
  const images = new Map(ai.images.map((i) => [i.id, i]))
  const ev = result.evaluated_strokes
  return (
    <div className="flex flex-col gap-3 text-sm">
      {result.insufficient_evidence && (
        <p className="rounded-md border border-warning/40 bg-warning/10 p-2 text-xs">
          Insufficient evidence for technical conclusions: see limitations.
        </p>
      )}
      <p>{result.summary}</p>
      <div className="flex flex-wrap gap-2 text-xs">
        <Badge>Strokes detected: {ev.detected}</Badge>
        <Badge>Evaluable: {ev.evaluable}</Badge>
        <Badge>Forehand: {ev.forehand}</Badge>
        <Badge>Backhand: {ev.backhand}</Badge>
        <Badge>Overall confidence: {pct(result.confidence)}</Badge>
      </div>
      {result.by_stroke_type.map((s) => (
        <div key={s.stroke_type}>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {humanize(s.stroke_type)} · {s.evaluable}/{s.detected} evaluable
          </p>
          <p className="text-xs">{s.summary}</p>
        </div>
      ))}
      {result.issues.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="font-medium">Issues ({result.issues.length})</p>
          {[...result.issues]
            .sort((a, b) => a.priority - b.priority)
            .map((issue) => (
              <IssueCard
                key={issue.id}
                issue={issue}
                images={images}
                canDeepDive={ai.kind === 'session'}
                busy={busy}
                onDeepDive={(sid) => onDeepDive(issue, sid)}
              />
            ))}
        </div>
      )}
      {result.phases.map((ph, i) => (
        <div key={i} className="rounded-md border p-2">
          <p className="text-xs font-semibold uppercase tracking-wide">{humanize(ph.phase)}</p>
          <Bullets label="Observed" items={ph.observed} />
          <Bullets label="Hypotheses (not proven by the data)" items={ph.hypotheses} />
          <Bullets label="Not observable" items={ph.not_observable} />
          <EvidenceThumbs refs={ph.evidence} images={images} />
        </div>
      ))}
      {result.positive_executions.length > 0 && (
        <div>
          <p className="font-medium">Positive executions</p>
          <ul className="list-disc pl-5 text-xs">
            {result.positive_executions.map((p) => (
              <li key={p.stroke_id}>
                <span className="font-mono">{p.stroke_id}</span>
                {p.timestamp_s != null && ` @ ${formatClock(p.timestamp_s, 1)}`}: {p.description}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.recommendations.length > 0 && (
        <div>
          <p className="font-medium">Improvement priorities</p>
          <ol className="list-decimal pl-5 text-xs">
            {[...result.recommendations]
              .sort((a, b) => a.priority - b.priority)
              .map((r) => (
                <li key={r.priority}>
                  <strong>{r.title}</strong>: {r.rationale}
                  <Exercises items={r.exercises} />
                </li>
              ))}
          </ol>
        </div>
      )}
      <Bullets label="Limitations" items={result.limitations} />
      <p className="text-[11px] text-muted-foreground">
        AI interpretation of engine measurements ({result.adapter}, {result.model},{' '}
        {result.analysis_version}). Biomechanical accuracy is not yet validated on real footage.
      </p>
    </div>
  )
}

function IssueCard({
  issue,
  images,
  canDeepDive,
  busy,
  onDeepDive,
}: {
  issue: AiIssue
  images: Map<string, AiEvidenceImage>
  canDeepDive: boolean
  busy: string | null
  onDeepDive: (strokeId: string) => void
}) {
  const [stroke, setStroke] = useState(issue.stroke_ids[0] ?? '')
  return (
    <div className="flex flex-col gap-1.5 rounded-md border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">#{issue.priority}</span>
        <span className="font-medium">{issue.name}</span>
        <Badge>{humanize(issue.stroke_type)}</Badge>
        <Badge variant={issue.nature === 'technical_error' ? 'warning' : 'neutral'}>
          {humanize(issue.nature)}
        </Badge>
        <Badge variant={issue.relevance === 'high' ? 'danger' : 'neutral'}>
          {issue.relevance} relevance
        </Badge>
        <span className="font-mono text-xs">
          {issue.occurrences}/{issue.evaluable_strokes} evaluable ({Math.round(issue.percentage)}%)
        </span>
        <span className="text-xs text-muted-foreground">confidence {pct(issue.confidence)}</span>
      </div>
      <p className="text-xs">{issue.description}</p>
      <Bullets label="Observed" items={issue.observations} />
      <p className="text-xs">
        <span className="font-semibold">Interpretation: </span>
        {issue.biomechanical_explanation}
      </p>
      <Bullets label="Alternative explanations" items={issue.alternative_explanations} />
      <Exercises items={issue.recommended_exercises} />
      <p className="font-mono text-[11px] text-muted-foreground">
        Strokes: {issue.stroke_ids.join(', ')}
        {issue.timestamps_s.length > 0 &&
          ` · at ${issue.timestamps_s.map((t) => formatClock(t, 1)).join(', ')}`}
      </p>
      <EvidenceThumbs refs={issue.evidence} images={images} />
      {canDeepDive && issue.stroke_ids.length > 0 && (
        <div className="flex items-center gap-2">
          <Select
            aria-label={`Stroke for ${issue.name}`}
            value={stroke}
            onChange={(e) => setStroke(e.target.value)}
            className="h-8 w-auto text-xs"
          >
            {issue.stroke_ids.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() => onDeepDive(stroke)}
          >
            {busy === `deep-${issue.id}` ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Deep-dive this stroke
          </Button>
        </div>
      )}
    </div>
  )
}

function Bullets({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="text-xs">
      <span className="font-semibold">{label}:</span>
      <ul className="list-disc pl-5">
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  )
}

function Exercises({ items }: { items: Exercise[] }) {
  if (items.length === 0) return null
  return (
    <ul className="mt-1 list-[circle] pl-5 text-xs">
      {items.map((x, i) => (
        <li key={i}>
          <strong>{x.name}</strong>: {x.description}
          {x.cue && <> · cue: “{x.cue}”</>}
          {x.dosage && <> · {x.dosage}</>}
          {x.how_to_verify && <> · verify: {x.how_to_verify}</>}
        </li>
      ))}
    </ul>
  )
}

function EvidenceThumbs({
  refs,
  images,
}: {
  refs: EvidenceRef[]
  images: Map<string, AiEvidenceImage>
}) {
  const imgs = refs.filter((r) => r.kind === 'image').flatMap((r) => images.get(r.ref) ?? [])
  if (imgs.length === 0) return null
  return (
    <div className="flex flex-wrap gap-2">
      {imgs.map((img) => (
        <a key={img.id} href={img.url} target="_blank" rel="noreferrer" className="block">
          <img
            src={img.url}
            alt={`${humanize(img.role)} at ${formatClock(img.timestamp_s, 2)}`}
            className="h-20 rounded border object-cover"
            loading="lazy"
          />
        </a>
      ))}
    </div>
  )
}
