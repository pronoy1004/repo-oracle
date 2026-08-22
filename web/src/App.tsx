import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import type { Repo, Source } from './api'
import { Answer, RepoCard, SourceViewer, Sources, type Citation } from './components'

type Turn = { role: 'user' | 'assistant'; text: string; sources?: Source[] }

const SUGGESTIONS = [
  'What does this project do, and how is it structured?',
  'Where are the HTTP endpoints defined?',
  'How does configuration get loaded?',
  'Walk me through the main request flow.',
]

export default function App() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [active, setActive] = useState<string>('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [ingestUrl, setIngestUrl] = useState('')
  const [ingestLog, setIngestLog] = useState<string[]>([])
  const [file, setFile] = useState<{ path: string; text: string } | null>(null)
  const [line, setLine] = useState(1)
  const [loadingFile, setLoadingFile] = useState(false)
  const [error, setError] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  const sessionId = useRef(Math.random().toString(36).slice(2))

  const refresh = useCallback(async () => {
    try {
      setRepos(await api.listRepos())
    } catch (e) {
      setError(String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setFile(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const openCitation = async (c: Citation) => {
    if (!active) return
    setLine(c.line)
    setLoadingFile(true)
    try {
      setFile(await api.fetchFile(active, c.path))
    } catch {
      setFile({ path: c.path, text: '// not in the index — the file was skipped at ingest time' })
    } finally {
      setLoadingFile(false)
    }
  }

  const ingest = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ingestUrl.trim()) return
    setError('')
    setIngestLog([])
    const kind = /^https?:\/\//.test(ingestUrl) ? 'git' : 'path'
    try {
      const { repo_id } = await api.startIngest(ingestUrl.trim(), '', kind)
      setIngestUrl('')
      await api.streamIngest(repo_id, (event, data) => {
        if (event === 'progress') setIngestLog((l) => [...l, `${data.type}: ${data.detail ?? ''}`])
        if (event === 'done') {
          refresh()
          if (data.status === 'done') setActive(repo_id)
          else setError(data.error ?? 'ingest failed')
        }
      })
      refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  const ask = async (question: string) => {
    if (!question.trim() || !active || busy) return
    setBusy(true)
    setError('')
    setInput('')
    setTurns((t) => [...t, { role: 'user', text: question }, { role: 'assistant', text: '' }])
    try {
      await api.streamChat({ repo_id: active, message: question, session_id: sessionId.current }, (event, data) => {
        setTurns((t) => {
          const next = [...t]
          const last = next[next.length - 1]
          if (event === 'token') next[next.length - 1] = { ...last, text: last.text + data.t }
          if (event === 'sources') next[next.length - 1] = { ...last, sources: data.sources }
          return next
        })
      })
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const selectRepo = (id: string) => {
    setActive(id)
    setTurns([])
    setFile(null)
    sessionId.current = Math.random().toString(36).slice(2)
  }

  const current = repos.find((r) => r.id === active)
  const last = turns[turns.length - 1]
  const status = busy
    ? 'Retrieving and answering'
    : last?.role === 'assistant' && last.text
      ? `Answer complete, ${last.sources?.length ?? 0} excerpts retrieved`
      : ''


  return (
    <div className="flex h-full flex-col">
      <p className="sr-only" role="status" aria-live="polite">
        {status}
      </p>
      <header className="flex items-center justify-between border-b border-edge px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-sm font-semibold text-accent">repo-oracle</span>
          <span className="hidden text-xs text-dim sm:inline">
            ask a codebase a question, get a cited answer
          </span>
        </div>
        <a
          href="https://github.com/pronoy1004/codebase-cartography"
          target="_blank"
          rel="noreferrer"
          className="text-xs text-dim underline decoration-dotted hover:text-accent"
          title="The repository-summary layer is adapted from this earlier project"
        >
          map layer from codebase-cartography ↗
        </a>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Repos */}
        <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-r border-edge p-3">
          <form onSubmit={ingest} className="flex flex-col gap-2">
            <input
              value={ingestUrl}
              onChange={(e) => setIngestUrl(e.target.value)}
              placeholder="https://github.com/owner/repo"
              aria-label="Git URL of the repository to index"
              className="rounded-md border border-edge bg-panel px-2 py-1.5 text-xs focus:border-accent"
            />
            <button
              type="submit"
              className="rounded-md bg-accent px-2 py-1.5 text-xs font-medium text-ink hover:opacity-90"
            >
              Index a repository
            </button>
          </form>

          {ingestLog.length > 0 && (
            <div
              role="status"
              aria-live="polite"
              aria-label="Indexing progress"
              className="max-h-40 overflow-y-auto rounded-md border border-edge bg-panel p-2 font-mono text-[0.7rem] leading-relaxed text-dim"
            >
              {ingestLog.map((l, i) => (
                <div key={i} className="truncate" title={l}>
                  {l}
                </div>
              ))}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            {repos.map((r) => (
              <RepoCard
                key={r.id}
                repo={r}
                active={r.id === active}
                onSelect={() => selectRepo(r.id)}
                onDelete={async () => {
                  await api.deleteRepo(r.id)
                  if (r.id === active) setActive('')
                  refresh()
                }}
              />
            ))}
            {!repos.length && (
              <p className="text-xs text-dim">Nothing indexed yet. Paste a GitHub URL above.</p>
            )}
          </div>
        </aside>

        {/* Chat */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto px-6 py-5">
            {!active && (
              /* The first thirty seconds decide whether someone trusts this, so the empty
                 state shows what an answer looks like rather than describing it. */
              <div className="mx-auto mt-12 max-w-lg sm:mt-20">
                <h1 className="text-xl font-semibold tracking-tight text-neutral-100">
                  Ask a codebase a question.
                </h1>
                <p className="mt-2 text-sm leading-relaxed text-dim">
                  Point it at a repository. It reads the code, writes a short summary of the
                  architecture, and indexes both. Then every answer it gives you cites the
                  lines it came from.
                </p>

                <div
                  aria-hidden="true"
                  className="mt-6 rounded-lg border border-edge bg-panel/60 p-3.5 text-[0.82rem] leading-relaxed"
                >
                  <p className="text-neutral-300">
                    Routes are registered in{' '}
                    <span className="cite pointer-events-none">src/flask/app.py:1122</span>, and
                    dispatched by <code className="font-mono text-neutral-400">full_dispatch_request</code>{' '}
                    at <span className="cite pointer-events-none">src/flask/app.py:880</span>.
                  </p>
                  <p className="mt-2 text-xs text-dim">
                    Click a citation and that file opens beside the answer, at that line.
                  </p>
                </div>

                <ol className="mt-6 flex flex-col gap-1.5 text-sm text-dim">
                  <li>
                    <span className="text-neutral-200">Paste a GitHub URL</span> in the panel on
                    the left.
                  </li>
                  <li>
                    <span className="text-neutral-200">Wait for the index.</span> A few minutes
                    for a mid-sized repo; progress streams as it goes.
                  </li>
                  <li>
                    <span className="text-neutral-200">Ask, then check the citation.</span> That
                    is the whole loop.
                  </li>
                </ol>
              </div>
            )}

            {active && !turns.length && (
              <div className="mx-auto max-w-2xl">
                <p className="mb-3 text-sm text-dim">
                  Indexed and ready. Something to start with:
                </p>
                <div className="flex flex-col gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => ask(s)}
                      className="rounded-lg border border-edge bg-panel px-3 py-2 text-left text-sm hover:border-neutral-600"
                    >
                      {s}
                    </button>
                  ))}
                </div>
                {current?.commit && (
                  <p className="mt-4 font-mono text-[0.7rem] text-dim">
                    indexed at {current.commit.slice(0, 8)} · {current.indexed_at}
                  </p>
                )}
              </div>
            )}

            <div className="mx-auto flex max-w-2xl flex-col gap-5">
              {turns.map((turn, i) =>
                turn.role === 'user' ? (
                  <div key={i} className="self-end rounded-2xl bg-panel px-3.5 py-2 text-sm">
                    {turn.text}
                  </div>
                ) : (
                  <div key={i}>
                    {turn.text ? (
                      <Answer text={turn.text} onCite={openCitation} />
                    ) : (
                      <span className="text-sm text-dim">retrieving…</span>
                    )}
                    <Sources sources={turn.sources ?? []} onCite={openCitation} />
                  </div>
                ),
              )}
              <div ref={bottom} />
            </div>
          </div>

          {error && (
            <div
              role="alert"
              className="border-t border-red-900/50 bg-red-950/30 px-6 py-2 text-xs text-red-300"
            >
              {error}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault()
              ask(input)
            }}
            className="border-t border-edge p-3"
          >
            <div className="mx-auto flex max-w-2xl gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!active || busy}
                placeholder={
                  active
                    ? 'Ask about this codebase…'
                    : repos.length
                      ? 'Pick a repository on the left to start'
                      : 'Index a repository first'
                }
                aria-label="Ask a question about this codebase"
                className="flex-1 rounded-lg border border-edge bg-panel px-3 py-2 text-sm focus:border-accent disabled:opacity-50"
              />
              <button
                disabled={!active || busy}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-ink disabled:opacity-40"
              >
                {busy ? '…' : 'Ask'}
              </button>
            </div>
          </form>
        </main>

        {/* Source */}
        <aside
          className={`shrink-0 border-l border-edge bg-ink lg:block lg:static lg:w-[30rem] ${
            file || loadingFile
              ? 'fixed inset-y-0 right-0 z-30 w-full max-w-[30rem] shadow-2xl shadow-black/60 lg:shadow-none'
              : 'hidden'
          }`}
        >
          <SourceViewer file={file} line={line} loading={loadingFile} onClose={() => setFile(null)} />
        </aside>
      </div>
    </div>
  )
}
