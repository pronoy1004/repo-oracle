/**
 * The API client. Two of these calls stream, and both stream Server-Sent Events over a
 * POST or a GET that EventSource cannot express (POST body, custom header), so SSE is
 * parsed by hand off the fetch body reader. That is about fifteen lines and avoids
 * inventing a second protocol just to satisfy EventSource.
 */

export type Source = {
  id: number
  path: string
  start_line: number
  end_line: number
  lang: string
  tier: 'code' | 'map'
  score: number
  how: string
  location: string
}

export type Repo = {
  id: string
  source: string
  status: 'running' | 'done' | 'error'
  error?: string | null
  commit?: string | null
  files?: number
  chunks?: number
  map_chunks?: number
  indexed_at?: string
  truncated?: boolean
}

const key = () => localStorage.getItem('oracle_key') ?? ''

const headers = () => {
  const h: Record<string, string> = { 'content-type': 'application/json' }
  if (key()) h['X-API-Key'] = key()
  return h
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: headers() })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail ?? res.statusText)
  return res.json()
}

export const listRepos = () => json<{ repos: Repo[] }>('/repos').then((r) => r.repos)

export const startIngest = (url: string, ref: string, kind: 'git' | 'path') =>
  json<{ repo_id: string }>('/repos', {
    method: 'POST',
    body: JSON.stringify({ url, ref: ref || null, kind }),
  })

export const deleteRepo = (id: string) => json(`/repos/${id}`, { method: 'DELETE' })

export const fetchFile = (repo: string, path: string) =>
  json<{ path: string; text: string }>(`/repos/${repo}/file?path=${encodeURIComponent(path)}`)

/** Read an SSE body, calling back with (event, data) for each frame. */
async function readSSE(res: Response, on: (event: string, data: any) => void) {
  if (!res.ok || !res.body) throw new Error(res.statusText)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const event = /^event: (.+)$/m.exec(frame)?.[1] ?? 'message'
      const data = /^data: (.+)$/m.exec(frame)?.[1]
      if (data) on(event, JSON.parse(data))
    }
  }
}

export const streamIngest = (id: string, on: (event: string, data: any) => void) =>
  fetch(`/repos/${id}/events`, { headers: headers() }).then((r) => readSSE(r, on))

export const streamChat = (
  body: { repo_id: string; message: string; session_id: string },
  on: (event: string, data: any) => void,
) =>
  fetch('/chat', { method: 'POST', headers: headers(), body: JSON.stringify(body) }).then((r) =>
    readSSE(r, on),
  )
