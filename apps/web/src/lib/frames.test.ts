import type { PoseSequence } from '@tpa/contracts'
import { describe, expect, it } from 'vitest'

import { clampFrame, containRect, frameAtTime, poseForFrame, timeForFrame } from './frames'

describe('frame/time conversion', () => {
  it('maps seek target back to the same frame for common frame rates', () => {
    for (const fps of [24, 25, 29.97, 30, 50, 59.94, 60, 120, 240]) {
      for (const i of [0, 1, 2, 99, 1234]) {
        expect(frameAtTime(timeForFrame(i, fps), fps)).toBe(i)
      }
    }
  })

  it('treats exact frame boundaries as the new frame', () => {
    expect(frameAtTime(1 / 30, 30)).toBe(1)
    expect(frameAtTime(0.1, 30)).toBe(3)
  })

  it('clamps frames', () => {
    expect(clampFrame(-3, 10)).toBe(0)
    expect(clampFrame(12, 10)).toBe(9)
  })
})

describe('poseForFrame', () => {
  const seq: PoseSequence = {
    estimator: 'x',
    model: 'x',
    image_width: 10,
    image_height: 10,
    fps: 30,
    frame_stride: 2,
    limitations: [],
    frames: [0, 2, 4, 6].map((i) => ({
      frame_index: i,
      timestamp_s: i / 30,
      detected: true,
      keypoints: [],
    })),
  }

  it('returns the exact analysed frame', () => {
    expect(poseForFrame(seq, 4)?.frame_index).toBe(4)
  })

  it('never substitutes a neighbouring pose for an unanalysed frame', () => {
    expect(poseForFrame(seq, 3)).toBeNull()
    expect(poseForFrame(seq, 8)).toBeNull()
    expect(poseForFrame(null, 0)).toBeNull()
  })
})

describe('containRect', () => {
  it('letterboxes a portrait video in a landscape box', () => {
    expect(containRect(1600, 900, 1080, 1920)).toEqual({
      x: 546.875,
      y: 0,
      width: 506.25,
      height: 900,
    })
  })
  it('handles unknown sizes', () => {
    expect(containRect(100, 100, 0, 0).width).toBe(0)
  })
})
