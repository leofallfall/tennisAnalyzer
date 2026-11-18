/**
 * ILLUSTRATIVE EXAMPLE DATA — NOT ANALYSIS OUTPUT.
 *
 * Used only to show the intended layout of screens whose computer-vision backend
 * does not exist yet. Every component rendering this data must display
 * <PlaceholderNotice />. Never mix these values with real results.
 */

export interface ExampleIssue {
  id: string
  title: string
  stroke: 'forehand' | 'backhand'
  occurrences: number
  evaluable: number
  confidence: 'high' | 'medium' | 'low'
  category: 'recurring' | 'isolated' | 'potentially_significant' | 'positive_reference'
  significance: string
  limitation: string
  timestamps: number[]
}

export const EXAMPLE_ISSUES: ExampleIssue[] = [
  {
    id: 'ex-1',
    title: 'Late preparation (unit turn completed after bounce)',
    stroke: 'forehand',
    occurrences: 19,
    evaluable: 35,
    confidence: 'medium',
    category: 'recurring',
    significance: 'Compresses the forward swing; correlates with late contact.',
    limitation: 'Bounce time estimated from player motion only.',
    timestamps: [12.4, 31.9, 58.2, 77.0],
  },
  {
    id: 'ex-2',
    title: 'Inconsistent contact distance from body',
    stroke: 'backhand',
    occurrences: 11,
    evaluable: 24,
    confidence: 'low',
    category: 'potentially_significant',
    significance: 'Cramped contacts reduce extension and racket-head speed.',
    limitation: 'Requires a behind/front camera view; 9 backhands excluded.',
    timestamps: [44.1, 90.3],
  },
  {
    id: 'ex-3',
    title: 'Limited hip rotation before forward swing',
    stroke: 'forehand',
    occurrences: 7,
    evaluable: 18,
    confidence: 'low',
    category: 'recurring',
    significance: 'Kinetic chain may rely on arm rather than trunk.',
    limitation: 'Hip rotation is a 2D proxy; evaluable on 18 of 35 forehands.',
    timestamps: [21.7, 66.8],
  },
  {
    id: 'ex-4',
    title: 'Stable head through contact',
    stroke: 'backhand',
    occurrences: 15,
    evaluable: 20,
    confidence: 'medium',
    category: 'positive_reference',
    significance: 'Good reference executions for comparison.',
    limitation: 'Camera motion not compensated.',
    timestamps: [52.5],
  },
]

export const EXAMPLE_SESSION_SUMMARY = {
  candidateStrokes: 71,
  observableStrokes: 59,
  forehands: 35,
  backhands: 24,
  excluded: 12,
}

export const EXAMPLE_PHASES = (duration: number) => {
  const d = duration || 1
  const names = ['Preparation', 'Backswing', 'Forward swing', 'Contact', 'Follow-through']
  const cuts = [0, 0.22, 0.45, 0.63, 0.68, 1]
  return names.map((label, i) => ({
    label,
    start_s: cuts[i]! * d,
    end_s: cuts[i + 1]! * d,
    example: true,
  }))
}

export const EXAMPLE_MEASUREMENTS = [
  {
    metric: 'Elbow angle at contact (2D)',
    value: '142°',
    confidence: 'medium',
    observability: 'observable',
  },
  {
    metric: 'Knee flexion at loading (2D)',
    value: '31°',
    confidence: 'medium',
    observability: 'observable',
  },
  {
    metric: 'Shoulder–hip separation',
    value: 'Unavailable',
    confidence: '—',
    observability: 'not_observable',
  },
  {
    metric: 'Contact height',
    value: '0.82 torso lengths',
    confidence: 'low',
    observability: 'partially_observable',
  },
] as const
