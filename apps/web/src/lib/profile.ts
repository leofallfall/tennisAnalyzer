/**
 * Player profile, stored in this browser only (localStorage) until the
 * PlayerProfile API exists. See docs/DATA_MODEL.md.
 */
export interface PlayerProfile {
  name: string
  dominantHand: 'right' | 'left' | ''
  backhand: 'one_handed' | 'two_handed' | ''
  level: 'intermediate' | 'advanced' | 'competitive' | ''
  yearsPlaying: string
  goals: string
}

const KEY = 'tpa.playerProfile.v1'

export const EMPTY_PROFILE: PlayerProfile = {
  name: '',
  dominantHand: '',
  backhand: '',
  level: '',
  yearsPlaying: '',
  goals: '',
}

export function loadProfile(): PlayerProfile {
  try {
    const raw = localStorage.getItem(KEY)
    return raw
      ? { ...EMPTY_PROFILE, ...(JSON.parse(raw) as Partial<PlayerProfile>) }
      : EMPTY_PROFILE
  } catch {
    return EMPTY_PROFILE
  }
}

export function saveProfile(p: PlayerProfile): void {
  localStorage.setItem(KEY, JSON.stringify(p))
}
