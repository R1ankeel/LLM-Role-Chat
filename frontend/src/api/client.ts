export const API_BASE: string = import.meta.env.VITE_API_BASE || '/api'

export class ApiError extends Error {
  status: number
  detail: string
  rateLimit: boolean

  constructor(status: number, detail: string, rateLimit = false) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.rateLimit = rateLimit
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, unknown>
  signal?: AbortSignal
  /** Raw body (e.g. FormData) sent as-is without JSON/Content-Type handling. */
  rawBody?: BodyInit
}

export async function toApiError(res: Response): Promise<ApiError> {
  let detail = `Ошибка ${res.status}`
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') detail = data.detail
  } catch {
    // non-JSON body — keep the default detail
  }
  return new ApiError(res.status, detail, res.status === 429)
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, rawBody } = opts

  let url = `${API_BASE}${path}`
  if (query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }

  let initHeaders: Record<string, string> | undefined
  let initBody: BodyInit | null | undefined
  if (rawBody !== undefined) {
    initBody = rawBody
  } else if (body !== undefined) {
    initHeaders = { 'Content-Type': 'application/json' }
    initBody = JSON.stringify(body)
  }

  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers: initHeaders,
      body: initBody,
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Сеть недоступна. Проверьте, что backend запущен.')
  }

  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T

  const text = await res.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    return text as unknown as T
  }
}

export function parseRateLimitSeconds(detail: string): number | null {
  const match = /Подождите (\d+) сек/i.exec(detail)
  return match ? Number(match[1]) : null
}
