import { Check } from 'lucide-react'
import { useState } from 'react'

import { PageHeader } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input, Label, Select, Textarea } from '@/components/ui/input'
import { loadProfile, saveProfile, type PlayerProfile } from '@/lib/profile'

export function PlayerProfilePage() {
  const [profile, setProfile] = useState<PlayerProfile>(loadProfile)
  const [saved, setSaved] = useState(false)
  const set = <K extends keyof PlayerProfile>(k: K, v: PlayerProfile[K]) => {
    setProfile((p) => ({ ...p, [k]: v }))
    setSaved(false)
  }

  return (
    <>
      <PageHeader
        title="Player Profile"
        description="Context used to interpret your strokes (e.g. dominant hand decides forehand vs backhand side)."
      />
      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Technical profile</CardTitle>
          <CardDescription>
            Stored in this browser only until the profile API exists.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault()
              saveProfile(profile)
              setSaved(true)
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="name">Name</Label>
              <Input id="name" value={profile.name} onChange={(e) => set('name', e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="hand">Dominant hand</Label>
              <Select
                id="hand"
                value={profile.dominantHand}
                onChange={(e) =>
                  set('dominantHand', e.target.value as PlayerProfile['dominantHand'])
                }
              >
                <option value="">Select…</option>
                <option value="right">Right</option>
                <option value="left">Left</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="backhand">Backhand</Label>
              <Select
                id="backhand"
                value={profile.backhand}
                onChange={(e) => set('backhand', e.target.value as PlayerProfile['backhand'])}
              >
                <option value="">Select…</option>
                <option value="one_handed">One-handed</option>
                <option value="two_handed">Two-handed</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="level">Level</Label>
              <Select
                id="level"
                value={profile.level}
                onChange={(e) => set('level', e.target.value as PlayerProfile['level'])}
              >
                <option value="">Select…</option>
                <option value="intermediate">Intermediate</option>
                <option value="advanced">Advanced</option>
                <option value="competitive">Competitive / semi-competitive</option>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="years">Years playing</Label>
              <Input
                id="years"
                inputMode="numeric"
                value={profile.yearsPlaying}
                onChange={(e) => set('yearsPlaying', e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="goals">Technical goals</Label>
              <Textarea
                id="goals"
                value={profile.goals}
                onChange={(e) => set('goals', e.target.value)}
              />
            </div>
            <div className="flex items-center gap-3 sm:col-span-2">
              <Button type="submit">Save profile</Button>
              {saved && (
                <span className="flex items-center gap-1 text-xs text-success">
                  <Check className="size-3.5" /> Saved
                </span>
              )}
            </div>
          </form>
        </CardContent>
      </Card>
    </>
  )
}
