import { useEffect, useMemo, useRef } from 'react'
import { marked } from 'marked'
import type { Repo, Source } from './api'

export type Citation = { path: string; line: number }

/**
 * The answer, rendered.
 *
 * Citations are the point of this product, so they are not left as text: after markdown
 * rendering, any inline code that looks like `path/file.py:123` becomes a button that
 * opens the source panel at that line. One delegated click handler on the container,
 * rather than a React node per citation, because the HTML comes from marked as a string.
 */
export function Answer({ text, onCite }: { text: string; onCite: (c: Citation) => void }) {
  const html = useMemo(() => {
    const rendered = marked.parse(text, { async: false, breaks: true }) as string
    return rendered.replace(
      /<code>([\w./-]+\.\w+):(\d+)(?:-(\d+))?<\/code>/g,
      (_m, path, line) =>
        `<button class="cite" data-path="${path}" data-line="${line}">${path}:${line}</button>`,
    )
  }, [text])

  return (
    <div
      className="prose-answer text-[0.92rem]"
      onClick={(e) => {
        const el = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
        if (el) onCite({ path: el.dataset.path!, line: Number(el.dataset.line) })
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

/** What retrieval found this turn. Shown collapsed: useful when an answer looks wrong. */
export function Sources({ sources, onCite }: { sources: Source[]; onCite: (c: Citation) => void }) {
  if (!sources.length) return null
  return (
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer text-dim hover:text-neutral-300 select-none">
        {sources.length} excerpts retrieved
      </summary>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {sources.map((s) => (
          <button
            key={s.id}
            onClick={() => onCite({ path: s.path, line: s.start_line })}
            title={`${s.how} · score ${s.score}`}
            className={`rounded border px-1.5 py-0.5 font-mono text-[0.7rem] transition-colors ${
              s.tier === 'map'
                ? 'border-amber-900/60 bg-amber-950/40 text-amber-200/80 hover:bg-amber-900/40'
                : 'border-edge bg-panel text-dim hover:border-neutral-600 hover:text-neutral-200'
            }`}
          >
            {s.tier === 'map' ? '◆ ' : ''}
            {s.location}
          </button>
        ))}
      </div>
    </details>
  )
}

/** The cited file, opened at the cited line. Source comes from the index, not from disk. */
export function SourceViewer({
  file,
  line,
  loading,
  onClose,
}: {
  file: { path: string; text: string } | null
  line: number
  loading: boolean
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.querySelector('[data-hit="1"]')?.scrollIntoView({ block: 'center' })
  }, [file, line])

  if (loading)
    return <div className="p-4 text-sm text-dim">Loading…</div>
  if (!file)
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-sm text-dim">
        Click a citation to read the code it points at.
      </div>
    )

  const lines = file.text.split('\n')
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-edge px-3 py-2">
        <span className="truncate font-mono text-xs text-neutral-300">{file.path}</span>
        <button onClick={onClose} className="px-1 text-dim hover:text-neutral-200">
          ✕
        </button>
      </div>
      <div ref={ref} className="flex-1 overflow-auto">
        <pre className="min-w-max py-2 font-mono text-[0.72rem] leading-[1.55]">
          {lines.map((text, i) => {
            const n = i + 1
            const hit = n >= line - 1 && n <= line + 1
            return (
              <div
                key={n}
                data-hit={n === line ? '1' : undefined}
                className={hit ? 'bg-emerald-500/10' : undefined}
              >
                <span className="inline-block w-12 select-none pr-3 text-right text-neutral-600">
                  {n}
                </span>
                <span className={n === line ? 'text-emerald-200' : 'text-neutral-300'}>{text}</span>
              </div>
            )
          })}
        </pre>
      </div>
    </div>
  )
}

export function RepoCard({
  repo,
  active,
  onSelect,
  onDelete,
}: {
  repo: Repo
  active: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const name = repo.source.replace(/\/$/, '').split('/').slice(-2).join('/')
  return (
    <div
      onClick={onSelect}
      className={`group cursor-pointer rounded-lg border px-3 py-2 transition-colors ${
        active ? 'border-accent/60 bg-accent/10' : 'border-edge bg-panel hover:border-neutral-600'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm">{name}</span>
        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
          className="opacity-0 transition-opacity group-hover:opacity-100 text-dim hover:text-red-400"
          title="Remove this index"
        >
          ✕
        </button>
      </div>
      <div className="mt-0.5 truncate text-[0.7rem] text-dim">
        {repo.status === 'running' && 'indexing…'}
        {repo.status === 'error' && <span className="text-red-400">failed</span>}
        {repo.status === 'done' &&
          `${repo.chunks ?? '?'} chunks · ${repo.files ?? '?'} files${
            repo.map_chunks ? ' · map' : ''
          }`}
      </div>
    </div>
  )
}
