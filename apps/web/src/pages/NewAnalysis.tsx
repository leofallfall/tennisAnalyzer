import type { AnalysisMode, VideoRelation, ViewAngle } from '@tpa/contracts'
import { Crosshair, FileVideo, Layers, Loader2, Upload, X } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { IssuesList } from '@/components/analysis/panels'
import { ErrorAlert, PageHeader } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input, Label, Select, Textarea } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { api, ApiError, uploadVideo } from '@/lib/api'
import { formatBytes } from '@/lib/format'
import { cn } from '@/lib/utils'

const VIEW_OPTIONS: { value: ViewAngle; label: string }[] = [
  { value: 'unknown', label: 'Not specified' },
  { value: 'side_forehand', label: 'Side (forehand side)' },
  { value: 'side_backhand', label: 'Side (backhand side)' },
  { value: 'behind', label: 'Behind the player' },
  { value: 'front', label: 'Facing the player' },
  { value: 'diagonal', label: 'Diagonal' },
]

const ACCEPT = '.mp4,.mov,.m4v,.avi,.mkv,.webm,video/*'

interface PendingFile {
  file: File
  view: ViewAngle
}

export function NewAnalysisPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<AnalysisMode | null>(null)
  const [files, setFiles] = useState<PendingFile[]>([])
  const [relation, setRelation] = useState<VideoRelation | ''>('')
  const [title, setTitle] = useState('')
  const [focus, setFocus] = useState('')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState<ApiError | null>(null)

  const multi = mode === 'single_stroke'
  const needsRelation = files.length > 1
  const canSubmit = !!mode && files.length > 0 && (!needsRelation || relation !== '') && !busy

  const addFiles = (list: FileList | null) => {
    if (!list) return
    const added = Array.from(list).map((file) => ({ file, view: 'unknown' as ViewAngle }))
    setFiles((prev) => (multi ? [...prev, ...added].slice(0, 8) : added.slice(0, 1)))
    setError(null)
  }

  const submit = async () => {
    if (!mode) return
    setBusy(true)
    setError(null)
    try {
      const ids: string[] = []
      for (const [i, pf] of files.entries()) {
        const video = await uploadVideo(pf.file, (f) => setProgress((i + f) / files.length))
        ids.push(video.id)
      }
      const analysis = await api.createAnalysis({
        mode,
        title: title || undefined,
        focus: focus || undefined,
        videos: ids.map((id, i) => ({ video_id: id, camera_view: files[i]!.view })),
        video_relation: needsRelation ? (relation as VideoRelation) : 'single',
      })
      await api.startAnalysis(analysis.id)
      navigate(`/analyses/${analysis.id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'unknown', String(e)))
      setBusy(false)
    }
  }

  const issues =
    (error?.details?.issues as {
      code: string
      severity: 'error' | 'warning'
      message: string
    }[]) ?? []

  return (
    <>
      <PageHeader
        title="New analysis"
        description="Choose what you want to analyse, then upload your video."
      />
      <div className="grid max-w-4xl gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <ModeCard
            selected={mode === 'session'}
            onSelect={() => {
              setMode('session')
              setFiles((f) => f.slice(0, 1))
            }}
            icon={Layers}
            title="Session Analysis"
            text="A full practice session (rally). All observable forehands and backhands contribute to session statistics and recurring issues."
          />
          <ModeCard
            selected={mode === 'single_stroke'}
            onSelect={() => setMode('single_stroke')}
            icon={Crosshair}
            title="Single Stroke Analysis"
            text="Deep, frame-by-frame analysis of one stroke. Upload several camera angles of the same stroke, or several strokes to compare."
          />
        </div>

        {mode && (
          <Card>
            <CardHeader>
              <CardTitle>Video{multi ? 's' : ''}</CardTitle>
              <CardDescription>
                MP4, MOV, M4V, AVI, MKV or WebM. Smartphone footage is fine; 1080p at 60 fps or
                higher gives the most reliable results.
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <label
                className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center hover:bg-muted/60"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  addFiles(e.dataTransfer.files)
                }}
              >
                <Upload className="size-5 text-muted-foreground" aria-hidden />
                <span className="text-sm font-medium">Drop a video here or click to browse</span>
                <input
                  type="file"
                  accept={ACCEPT}
                  multiple={multi}
                  className="sr-only"
                  aria-label="Choose video file"
                  onChange={(e) => addFiles(e.target.files)}
                />
              </label>

              {files.length > 0 && (
                <ul className="flex flex-col gap-2">
                  {files.map((pf, i) => (
                    <li
                      key={`${pf.file.name}-${i}`}
                      className="flex flex-wrap items-center gap-3 rounded-md border p-2"
                    >
                      <FileVideo className="size-4 text-muted-foreground" aria-hidden />
                      <span className="min-w-0 flex-1 truncate text-sm">{pf.file.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatBytes(pf.file.size)}
                      </span>
                      {multi && (
                        <Select
                          aria-label={`Camera view for ${pf.file.name}`}
                          className="h-8 w-48 text-xs"
                          value={pf.view}
                          onChange={(e) =>
                            setFiles((prev) =>
                              prev.map((p, j) =>
                                j === i ? { ...p, view: e.target.value as ViewAngle } : p,
                              ),
                            )
                          }
                        >
                          {VIEW_OPTIONS.map((o) => (
                            <option key={o.value} value={o.value}>
                              {o.label}
                            </option>
                          ))}
                        </Select>
                      )}
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Remove ${pf.file.name}`}
                        onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                      >
                        <X />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}

              {needsRelation && (
                <fieldset className="flex flex-col gap-2 rounded-md border p-3">
                  <legend className="px-1 text-xs font-medium">
                    How are these videos related? (required)
                  </legend>
                  {(
                    [
                      [
                        'same_stroke_multi_angle',
                        'The same stroke filmed from different camera angles',
                      ],
                      ['different_strokes', 'Different strokes (to compare executions)'],
                    ] as const
                  ).map(([value, label]) => (
                    <label key={value} className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="relation"
                        value={value}
                        checked={relation === value}
                        onChange={() => setRelation(value)}
                      />
                      {label}
                    </label>
                  ))}
                </fieldset>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="title">Title (optional)</Label>
                  <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
                </div>
                {mode === 'single_stroke' && (
                  <div className="flex flex-col gap-1.5 sm:col-span-2">
                    <Label htmlFor="focus">Technical aspect to investigate (optional)</Label>
                    <Textarea
                      id="focus"
                      placeholder="e.g. Is my contact point too close to my body on the backhand?"
                      value={focus}
                      onChange={(e) => setFocus(e.target.value)}
                    />
                  </div>
                )}
              </div>

              {busy && (
                <div className="flex flex-col gap-1.5">
                  <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="size-3.5 animate-spin" aria-hidden /> Uploading and
                    validating…
                  </p>
                  <Progress value={progress} />
                </div>
              )}
              {error && (
                <div className="flex flex-col gap-2">
                  <ErrorAlert
                    title="Could not create the analysis"
                    message={error.message}
                    code={error.code}
                  />
                  <IssuesList issues={issues} />
                </div>
              )}
              <Button onClick={submit} disabled={!canSubmit} className="self-start">
                Upload and start processing
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  )
}

function ModeCard({
  selected,
  onSelect,
  icon: Icon,
  title,
  text,
}: {
  selected: boolean
  onSelect: () => void
  icon: typeof Layers
  title: string
  text: string
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'flex flex-col gap-2 rounded-lg border bg-card p-4 text-left transition-colors hover:border-primary/50',
        selected && 'border-primary ring-1 ring-primary',
      )}
    >
      <Icon className="size-5 text-primary" aria-hidden />
      <span className="text-sm font-semibold">{title}</span>
      <span className="text-xs text-muted-foreground">{text}</span>
    </button>
  )
}
