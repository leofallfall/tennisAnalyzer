import type { Analysis, AnalysisCreate } from '@tpa/contracts'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { saveProfile, EMPTY_PROFILE } from '@/lib/profile'
import { renderAt } from '@/test/utils'

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    uploadVideo: vi.fn(async () => ({ id: 'v1' })),
  }
})

afterEach(() => vi.restoreAllMocks())

async function chooseSessionVideo() {
  await userEvent.click(screen.getByText('Session Analysis'))
  await userEvent.upload(
    screen.getByLabelText('Choose video file'),
    new File(['a'], 'rally.mp4', { type: 'video/mp4' }),
  )
}

async function submitSession(): Promise<AnalysisCreate> {
  const create = vi
    .spyOn(api, 'createAnalysis')
    .mockResolvedValue({ id: 'a1' } as unknown as Analysis)
  vi.spyOn(api, 'startAnalysis').mockResolvedValue({} as never)
  await userEvent.click(screen.getByRole('button', { name: 'Upload and start processing' }))
  expect(create).toHaveBeenCalledTimes(1)
  return create.mock.calls[0]![0]
}

describe('dominant hand on a new analysis (M1.10)', () => {
  it('is pre-filled from the player profile and sent to the backend', async () => {
    saveProfile({ ...EMPTY_PROFILE, dominantHand: 'left', backhand: 'one_handed' })
    renderAt('/analyses/new')
    await chooseSessionVideo()
    expect(screen.getByLabelText("Player's dominant hand (optional)")).toHaveValue('left')
    expect(screen.getByLabelText("Player's backhand (optional)")).toHaveValue('one_handed')
    const body = await submitSession()
    expect(body.dominant_hand).toBe('left')
    expect(body.backhand).toBe('one_handed')
  })

  it('sends null when not specified, never a guess', async () => {
    renderAt('/analyses/new')
    await chooseSessionVideo()
    expect(screen.getByLabelText("Player's dominant hand (optional)")).toHaveValue('')
    const body = await submitSession()
    expect(body.dominant_hand).toBeNull()
    expect(body.backhand).toBeNull()
  })

  it('can be changed for this analysis only', async () => {
    saveProfile({ ...EMPTY_PROFILE, dominantHand: 'right' })
    renderAt('/analyses/new')
    await chooseSessionVideo()
    await userEvent.selectOptions(
      screen.getByLabelText("Player's dominant hand (optional)"),
      'left',
    )
    const body = await submitSession()
    expect(body.dominant_hand).toBe('left')
    expect(JSON.parse(localStorage.getItem('tpa.playerProfile.v1')!).dominantHand).toBe('right')
  })
})
