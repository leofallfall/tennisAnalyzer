import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: Error | null
  loading: boolean
  reload: () => void
}

interface Settled<T> {
  key: string
  data: T | null
  error: Error | null
}

/**
 * Minimal data-fetching hook (no external query library needed yet).
 * ``deps`` must be JSON-serialisable; they identify the request.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [tick, setTick] = useState(0)
  const [settled, setSettled] = useState<Settled<T>>({ key: '', data: null, error: null })
  const key = JSON.stringify([...deps, tick])
  const fnRef = useRef(fn)
  useEffect(() => {
    fnRef.current = fn
  })

  useEffect(() => {
    let cancelled = false
    fnRef
      .current()
      .then((data) => {
        if (!cancelled) setSettled({ key, data, error: null })
      })
      .catch((e: unknown) => {
        if (!cancelled)
          setSettled((s) => ({
            key,
            data: s.data,
            error: e instanceof Error ? e : new Error(String(e)),
          }))
      })
    return () => {
      cancelled = true
    }
  }, [key])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data: settled.data, error: settled.error, loading: settled.key !== key, reload }
}

/** Calls ``fn`` every ``ms`` while ``active``. */
export function useInterval(fn: () => void, ms: number, active: boolean): void {
  const ref = useRef(fn)
  useEffect(() => {
    ref.current = fn
  })
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => ref.current(), ms)
    return () => window.clearInterval(id)
  }, [ms, active])
}
