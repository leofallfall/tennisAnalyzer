import { render } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { vi } from 'vitest'

import { routes } from '@/routes'

export function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(<RouterProvider router={router} />)
}

/** Route fetch calls to canned JSON responses keyed by URL path. */
export function mockFetch(responses: Record<string, unknown>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    const path = url.replace(/^https?:\/\/[^/]+/, '')
    if (path in responses) {
      return new Response(JSON.stringify(responses[path]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(
      JSON.stringify({ error: { code: 'not_found', message: 'nope', details: {} } }),
      {
        status: 404,
      },
    )
  })
  vi.stubGlobal('fetch', fn)
  return fn
}
