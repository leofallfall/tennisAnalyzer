import type {
  Analysis,
  AnalysisCreate,
  AnalysisStatusInfo,
  AnalysisSummary,
  ApiErrorBody,
  PoseSequence,
  SystemInfo,
  Video,
} from '@tpa/contracts'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError(0, 'network_error', 'Cannot reach the API. Is the backend running?')
  }
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error
    throw new ApiError(
      res.status,
      err?.code ?? 'http_error',
      err?.message ?? `Request failed (${res.status})`,
      err?.details ?? {},
    )
  }
  return body as T
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  systemInfo: () => request<SystemInfo>('/api/system/info'),
  getVideo: (id: string) => request<Video>(`/api/videos/${id}`),
  listAnalyses: () => request<AnalysisSummary[]>('/api/analyses'),
  getAnalysis: (id: string) => request<Analysis>(`/api/analyses/${id}`),
  getStatus: (id: string) => request<AnalysisStatusInfo>(`/api/analyses/${id}/status`),
  createAnalysis: (body: AnalysisCreate) => request<Analysis>('/api/analyses', json(body)),
  startAnalysis: (id: string) =>
    request<AnalysisStatusInfo>(`/api/analyses/${id}/start`, { method: 'POST' }),
  getPose: (url: string) => request<PoseSequence>(url),
}

/** Upload with progress events (fetch has no upload progress). */
export function uploadVideo(file: File, onProgress?: (fraction: number) => void): Promise<Video> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/api/videos/upload`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total)
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', 'Upload failed: network error'))
    xhr.onload = () => {
      let body: unknown = null
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        /* non-JSON error */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Video)
      else {
        const err = (body as ApiErrorBody | null)?.error
        reject(
          new ApiError(
            xhr.status,
            err?.code ?? 'http_error',
            err?.message ?? `Upload failed (${xhr.status})`,
            err?.details ?? {},
          ),
        )
      }
    }
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}
