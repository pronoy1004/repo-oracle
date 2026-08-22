import { useEffect, useMemo, useRef, useState } from 'react'
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
        `<button type="button" class="cite" data-path="${path}" data-line="${line}" ` +
        `aria-label="Open ${path} at line ${line}">${path}:${line}</button>`,
    )
  }, [text])

  return (
    <div
      className="prose-answer text-[0.92rem]"
      onClick={(e) => {
        const el = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
        if (el) onCite({ path: el.dataset.path!, line: Number(el.dataset.line) })
      }}
      onKeyDown={(e) => {
        // Delegation covers click; native <button> turns Enter/Space into a click, so this
        // only guards the case where a citation ends up on a non-button element.
        if (e.key !== 'Enter' && e.key !== ' ') return
        const el = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
        if (el && el.tagName !== 'BUTTON') {
          e.preventDefault()
          onCite({ path: el.dataset.path!, line: Number(el.dataset.line) })
        }
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
  const [beat, setBeat] = useState(0)

  useEffect(() => {
    ref.current?.querySelector('[data-hit="1"]')?.scrollIntoView({ block: 'center' })
    setBeat((n) => n + 1)
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
    <div className="flex h-full flex-col" role="region" aria-label={`Source: ${file.path}`}>
      <div className="flex items-center justify-between border-b border-edge px-3 py-2">
        <span className="truncate font-mono text-xs text-neutral-300">{file.path}</span>
        <button
          onClick={onClose}
          aria-label="Close the source panel"
          className="rounded px-1 text-dim hover:text-neutral-200"
        >
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
                /* The cited row is keyed on the beat counter so it remounts when the target
                   changes, which is what replays the flash. Its neighbours keep a stable
                   key and never re-render. */
                key={n === line ? `hit-${beat}` : n}
                data-hit={n === line ? '1' : undefined}
                className={
                  n === line ? 'cite-flash bg-emerald-500/10' : hit ? 'bg-emerald-500/10' : undefined
                }
              >
                <span className="inline-block w-12 select-none pr-3 text-right text-gutter">
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
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect()
        }
      }}
      role="button"
      tabIndex={0}
      aria-pressed={active}
      /* A div rather than a button because it contains its own delete button, and nesting
         a button inside a button is invalid HTML. role + tabIndex + a key handler restore
         what the native element would have given us; the global :focus-visible rule paints
         the ring. */
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
          aria-label={`Remove the index for ${name}`}
          className="rounded text-dim opacity-0 transition-opacity hover:text-red-400
                     focus-visible:opacity-100 group-hover:opacity-100"
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
