import type { PoseFrame, PoseSequence } from '@tpa/contracts'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Pause, Play } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { formatClock } from '@/lib/format'
import { clampFrame, containRect, frameAtTime, poseForFrame, timeForFrame } from '@/lib/frames'
import { cn } from '@/lib/utils'

import { drawSkeleton } from './drawSkeleton'

export interface TimelineSegment {
  label: string
  start_s: number
  end_s: number
  /** Example/placeholder segments are drawn hatched and labelled. */
  example?: boolean
}

export interface VideoPlayerProps {
  src: string
  fps: number
  frameCount?: number | null
  pose?: PoseSequence | null
  segments?: TimelineSegment[]
  onFrameChange?: (frame: number, pose: PoseFrame | null) => void
  className?: string
}

const RATES = [0.1, 0.25, 0.5, 1]

/**
 * Frame-accurate player for constant-frame-rate proxies.
 * The skeleton is drawn client-side from the pose JSON so it can be toggled and
 * inspected frame by frame; frames that were not analysed show no skeleton.
 */
export function VideoPlayer({
  src,
  fps,
  frameCount,
  pose = null,
  segments = [],
  onFrameChange,
  className,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const [time, setTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [rate, setRate] = useState(1)
  const [showSkeleton, setShowSkeleton] = useState(true)

  const totalFrames = frameCount ?? Math.round(duration * fps)
  const frame = frameAtTime(time, fps)
  const currentPose = poseForFrame(pose, frame)

  const draw = useCallback(() => {
    const video = videoRef.current
    const canvas = canvasRef.current
    const box = boxRef.current
    if (!video || !canvas || !box) return
    const dpr = window.devicePixelRatio || 1
    const w = box.clientWidth
    const h = box.clientHeight
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    if (!showSkeleton || !pose) return
    const f = poseForFrame(pose, frameAtTime(video.currentTime, fps))
    if (!f?.detected) return
    drawSkeleton(ctx, f, containRect(w, h, video.videoWidth, video.videoHeight))
  }, [fps, pose, showSkeleton])

  // Keep time state and overlay in sync with the decoded frame.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    let handle = 0
    const hasRVFC = 'requestVideoFrameCallback' in HTMLVideoElement.prototype
    const tick = () => {
      setTime(video.currentTime)
      draw()
      handle = hasRVFC ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick)
    }
    handle = hasRVFC ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick)
    const onSeeked = () => {
      setTime(video.currentTime)
      draw()
    }
    video.addEventListener('seeked', onSeeked)
    window.addEventListener('resize', draw)
    return () => {
      if (hasRVFC) video.cancelVideoFrameCallback(handle)
      else cancelAnimationFrame(handle)
      video.removeEventListener('seeked', onSeeked)
      window.removeEventListener('resize', draw)
    }
  }, [draw])

  useEffect(() => {
    draw()
  }, [draw, showSkeleton])

  useEffect(() => {
    onFrameChange?.(frame, currentPose)
  }, [frame, currentPose, onFrameChange])

  const seekFrame = useCallback(
    (index: number) => {
      const video = videoRef.current
      if (!video) return
      video.pause()
      video.currentTime = timeForFrame(clampFrame(index, totalFrames || 1), fps)
    },
    [fps, totalFrames],
  )

  /** Relative step based on the element's (already updated) currentTime, not React state,
   * so rapid key presses accumulate correctly while seeks are still pending. */
  const step = useCallback(
    (delta: number) => {
      const video = videoRef.current
      if (!video) return
      seekFrame(frameAtTime(video.currentTime, fps) + delta)
    },
    [fps, seekFrame],
  )

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play()
    else v.pause()
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    const n = e.shiftKey ? 10 : 1
    if (e.key === 'ArrowRight' || e.key === '.') step(n)
    else if (e.key === 'ArrowLeft' || e.key === ',') step(-n)
    else if (e.key === ' ') togglePlay()
    else return
    e.preventDefault()
  }

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        ref={boxRef}
        tabIndex={0}
        onKeyDown={onKeyDown}
        aria-label="Video player. Arrow keys step one frame, Shift+arrow ten frames, Space plays or pauses."
        className="relative aspect-video w-full overflow-hidden rounded-md bg-black"
      >
        <video
          ref={videoRef}
          src={src}
          className="absolute inset-0 size-full object-contain"
          playsInline
          muted
          preload="auto"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onClick={togglePlay}
        />
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 size-full" />
        {pose && showSkeleton && currentPose == null && (
          <span className="absolute top-2 left-2 rounded-sm bg-black/60 px-1.5 py-0.5 text-[11px] text-white">
            Frame not analysed
          </span>
        )}
        {currentPose && !currentPose.detected && showSkeleton && (
          <span className="absolute top-2 left-2 rounded-sm bg-black/60 px-1.5 py-0.5 text-[11px] text-white">
            No pose detected in this frame
          </span>
        )}
      </div>

      <Timeline
        time={time}
        duration={duration}
        segments={segments}
        onSeek={(t) => {
          const v = videoRef.current
          if (v) v.currentTime = t
        }}
      />

      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="icon" variant="outline" aria-label="Back 10 frames" onClick={() => step(-10)}>
          <ChevronsLeft />
        </Button>
        <Button size="icon" variant="outline" aria-label="Previous frame" onClick={() => step(-1)}>
          <ChevronLeft />
        </Button>
        <Button size="icon" aria-label={playing ? 'Pause' : 'Play'} onClick={togglePlay}>
          {playing ? <Pause /> : <Play />}
        </Button>
        <Button size="icon" variant="outline" aria-label="Next frame" onClick={() => step(1)}>
          <ChevronRight />
        </Button>
        <Button
          size="icon"
          variant="outline"
          aria-label="Forward 10 frames"
          onClick={() => step(10)}
        >
          <ChevronsRight />
        </Button>
        <div className="ml-1 font-mono text-xs tabular-nums text-muted-foreground">
          <span className="text-foreground">{formatClock(time)}</span> / {formatClock(duration)} ·
          frame{' '}
          <span className="text-foreground" data-testid="frame-index">
            {frame}
          </span>
          {totalFrames ? ` / ${totalFrames - 1}` : ''}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={showSkeleton}
              disabled={!pose}
              onChange={(e) => setShowSkeleton(e.target.checked)}
            />
            Skeleton
          </label>
          <select
            aria-label="Playback speed"
            value={rate}
            onChange={(e) => {
              const r = Number(e.target.value)
              setRate(r)
              if (videoRef.current) videoRef.current.playbackRate = r
            }}
            className="h-8 rounded-md border border-input bg-card px-1.5 text-xs"
          >
            {RATES.map((r) => (
              <option key={r} value={r}>
                {r}×
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}

function Timeline({
  time,
  duration,
  segments,
  onSeek,
}: {
  time: number
  duration: number
  segments: TimelineSegment[]
  onSeek: (t: number) => void
}) {
  const pct = (t: number) => (duration > 0 ? (t / duration) * 100 : 0)
  return (
    <div className="flex flex-col gap-1">
      <input
        type="range"
        aria-label="Timeline"
        min={0}
        max={duration || 0}
        step={0.001}
        value={Math.min(time, duration || 0)}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="w-full accent-[var(--color-primary)]"
      />
      {segments.length > 0 && (
        <div className="relative h-6 rounded-sm bg-muted" aria-label="Phase segmentation">
          {segments.map((s) => (
            <button
              type="button"
              key={`${s.label}-${s.start_s}`}
              title={`${s.label}${s.example ? ' (example)' : ''}`}
              onClick={() => onSeek(s.start_s)}
              className={cn(
                'absolute inset-y-0 overflow-hidden border-r border-card px-1 text-left text-[10px] leading-6 font-medium text-secondary-foreground',
                s.example ? 'placeholder-hatch bg-warning/10' : 'bg-primary/15',
              )}
              style={{ left: `${pct(s.start_s)}%`, width: `${pct(s.end_s - s.start_s)}%` }}
            >
              {s.label}
            </button>
          ))}
          <div
            className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground"
            style={{ left: `${pct(time)}%` }}
          />
        </div>
      )}
    </div>
  )
}
