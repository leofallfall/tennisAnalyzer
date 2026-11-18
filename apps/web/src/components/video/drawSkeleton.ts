import type { PoseFrame } from '@tpa/contracts'

import type { Rect } from '@/lib/frames'
import { SKELETON_EDGES } from '@/lib/skeleton'

const LEFT = '#3ca0eb'
const RIGHT = '#ff8c28'
const CENTER = '#e6e6e6'
const LOW = 'rgba(160,160,160,0.6)'

export function drawSkeleton(
  ctx: CanvasRenderingContext2D,
  pose: PoseFrame,
  rect: Rect,
  minVisibility = 0.5,
): void {
  const byName = new Map(pose.keypoints.map((k) => [k.name, k]))
  const ok = (v: number | null) => v == null || v >= minVisibility
  const color = (name: string) =>
    name.startsWith('left') ? LEFT : name.startsWith('right') ? RIGHT : CENTER
  const px = (x: number) => rect.x + x * rect.width
  const py = (y: number) => rect.y + y * rect.height
  const lw = Math.max(1.5, rect.height / 260)

  ctx.lineCap = 'round'
  ctx.lineWidth = lw
  for (const [a, b] of SKELETON_EDGES) {
    const ka = byName.get(a)
    const kb = byName.get(b)
    if (!ka || !kb) continue
    ctx.strokeStyle = ok(ka.visibility) && ok(kb.visibility) ? color(b) : LOW
    ctx.beginPath()
    ctx.moveTo(px(ka.x), py(ka.y))
    ctx.lineTo(px(kb.x), py(kb.y))
    ctx.stroke()
  }
  for (const k of pose.keypoints) {
    ctx.fillStyle = ok(k.visibility) ? color(k.name) : LOW
    ctx.beginPath()
    ctx.arc(px(k.x), py(k.y), lw * 1.4, 0, Math.PI * 2)
    ctx.fill()
  }
}
