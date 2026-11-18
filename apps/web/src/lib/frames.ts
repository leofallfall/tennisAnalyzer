import type { PoseFrame, PoseSequence } from '@tpa/contracts'

/** Frame index shown for a playback time. A small epsilon absorbs float error. */
export function frameAtTime(time: number, fps: number): number {
  if (fps <= 0 || !Number.isFinite(time)) return 0
  return Math.max(0, Math.floor(time * fps + 1e-6))
}

/** Seek target for a frame: the middle of the frame interval is robust across browsers. */
export function timeForFrame(index: number, fps: number): number {
  return (Math.max(0, index) + 0.5) / fps
}

export function clampFrame(index: number, frameCount: number): number {
  return Math.min(Math.max(0, index), Math.max(0, frameCount - 1))
}

/**
 * Pose for a frame index. Returns the exact analysed frame, or null when that frame
 * was not analysed (stride > 1) — we never interpolate or reuse a neighbour's pose.
 */
export function poseForFrame(seq: PoseSequence | null, index: number): PoseFrame | null {
  if (!seq) return null
  const frames = seq.frames
  let lo = 0
  let hi = frames.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const f = frames[mid]!
    if (f.frame_index === index) return f
    if (f.frame_index < index) lo = mid + 1
    else hi = mid - 1
  }
  return null
}

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** Where a video with intrinsic size (vw, vh) is drawn inside a box with object-fit: contain. */
export function containRect(boxW: number, boxH: number, vw: number, vh: number): Rect {
  if (vw <= 0 || vh <= 0 || boxW <= 0 || boxH <= 0) return { x: 0, y: 0, width: 0, height: 0 }
  const scale = Math.min(boxW / vw, boxH / vh)
  const width = vw * scale
  const height = vh * scale
  return { x: (boxW - width) / 2, y: (boxH - height) / 2, width, height }
}
