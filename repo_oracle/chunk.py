"""Turn a repository on disk into retrievable chunks.

Two decisions worth stating, because chunking is where retrieval quality is won or lost:

1. Chunks follow *declaration* boundaries, not a fixed token window. A function cut in half
   retrieves badly and reads worse when it lands in the prompt, and code has visible
   boundaries that prose does not, so there is no excuse for ignoring them.
2. Every chunk carries its `path:start-end` in the embedded text. The location is part of
   what makes a chunk relevant ("where is the router configured" is half a path question),
   and it means a retrieved chunk can always be cited without a lookup.

ponytail: the boundary detector is a per-language regex over lines at column 0, not a real
parser. It handles the ~10 languages below and degrades to fixed windows for everything
else. Upgrade path if quality demands it: tree-sitter, one grammar per language, same
Chunk output so nothing downstream changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 1_000_000
MAX_CHUNK_LINES = 120
MIN_CHUNK_LINES = 8
WINDOW_OVERLAP = 10
# Minified bundles and generated blobs: technically text, useless to retrieve, expensive
# to embed. Average line length separates them from hand-written code cleanly.
MAX_MEAN_LINE_LEN = 400

SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", ".cache", ".idea", ".vscode",
    "site-packages", "coverage", ".pytest_cache", ".mypy_cache", ".tox", "bower_components",
})

SKIP_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".pdf", ".zip",
    ".gz", ".tar", ".bz2", ".xz", ".7z", ".jar", ".war", ".class", ".so", ".dylib",
    ".dll", ".exe", ".bin", ".wasm", ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".psd", ".sketch", ".db", ".sqlite",
})

# Lockfiles are hundreds of KB of noise that answer no question a human asks.
SKIP_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum", "uv.lock", "bun.lockb",
})

# Files whose *names* are worth knowing and whose *contents* are never worth indexing.
# The name answers "where does config live"; the value is somebody's key.
SECRET_RE = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|id_rsa.*|.*credentials.*|.*secret.*\.(json|ya?ml))$",
    re.IGNORECASE,
)

LANGS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".php": "php", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".swift": "swift", ".scala": "scala", ".sh": "shell", ".bash": "shell",
    ".sql": "sql", ".md": "markdown", ".rst": "markdown", ".txt": "text",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".html": "html", ".css": "css", ".scss": "css", ".vue": "vue", ".svelte": "svelte",
}

# A declaration that starts a new chunk. Matched against whole lines, anchored, so an
# indented method body does not split its parent class.
BOUNDARY = {
    "python": re.compile(r"^(?:@\w|(?:async\s+)?def\s|class\s|if\s+__name__)"),
    "javascript": re.compile(
        r"^(?:export\s|(?:async\s+)?function\s|class\s|const\s+\w+\s*=\s*(?:async\s*)?\(|"
        r"module\.exports|describe\(|it\()"
    ),
    "go": re.compile(r"^(?:func\s|type\s|var\s\(|const\s\()"),
    "rust": re.compile(r"^(?:pub\s+)?(?:async\s+)?(?:fn\s|struct\s|enum\s|impl\s|trait\s|mod\s)"),
    "java": re.compile(r"^\s{0,4}(?:public|private|protected|static|final|abstract|class|@)\S*\s"),
    "ruby": re.compile(r"^(?:def\s|class\s|module\s)"),
    "php": re.compile(r"^(?:function\s|class\s|trait\s|interface\s|namespace\s)"),
    "markdown": re.compile(r"^#{1,3}\s"),
    "sql": re.compile(r"^(?:CREATE|ALTER|DROP|INSERT|--\s*name)", re.IGNORECASE),
}
BOUNDARY["typescript"] = BOUNDARY["javascript"]
BOUNDARY["vue"] = BOUNDARY["javascript"]
BOUNDARY["svelte"] = BOUNDARY["javascript"]
BOUNDARY["kotlin"] = BOUNDARY["java"]
BOUNDARY["csharp"] = BOUNDARY["java"]
BOUNDARY["scala"] = BOUNDARY["java"]
BOUNDARY["c"] = re.compile(r"^[A-Za-z_][\w\s\*]*\([^;]*$|^(?:struct|enum|typedef|#define)\s")
BOUNDARY["cpp"] = BOUNDARY["c"]
BOUNDARY["swift"] = re.compile(r"^(?:public\s+|private\s+|internal\s+)?(?:func\s|class\s|struct\s|enum\s|extension\s|protocol\s)")
BOUNDARY["shell"] = re.compile(r"^(?:\w+\s*\(\)\s*\{|function\s)")


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    lang: str
    text: str
    tier: str = "code"
    symbols: str = field(default="")

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def embed_text(self) -> str:
        """What actually gets embedded. The location leads because it carries meaning."""
        return f"{self.location}\n{self.text}"


def _is_skippable(rel: Path) -> bool:
    if any(part in SKIP_DIRS for part in rel.parts[:-1]):
        return True
    if rel.name in SKIP_NAMES or rel.suffix.lower() in SKIP_EXTS:
        return True
    return rel.name.startswith(".") and rel.suffix not in LANGS and rel.name != ".env"


def walk(repo: Path, max_files: int = 4000) -> list[tuple[str, str]]:
    """Every indexable file in `repo`, as (relative path, text).

    Secret-shaped files come back with a placeholder body: the path is useful context,
    the contents are not ours to embed or to show.
    """
    out: list[tuple[str, str]] = []
    for path in sorted(repo.rglob("*")):
        if len(out) >= max_files:
            break
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(repo)
        if _is_skippable(rel):
            continue
        posix = rel.as_posix()
        if SECRET_RE.search(posix):
            out.append((posix, f"[secret-shaped file: contents deliberately not indexed]\n"))
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8000]:  # binary that slipped past the extension list
            continue
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if not lines:
            continue
        if len(text) / len(lines) > MAX_MEAN_LINE_LEN:
            continue
        out.append((posix, text))
    return out


def _boundaries(lines: list[str], lang: str) -> list[int]:
    """Indices where a new declaration starts. Always includes 0."""
    rx = BOUNDARY.get(lang)
    if rx is None:
        return [0]
    hits = [0] + [i for i, line in enumerate(lines) if i and rx.match(line)]
    return sorted(set(hits))


def _sections(lines: list[str], lang: str) -> list[tuple[int, int]]:
    """Half-open [start, end) line ranges, merged so nothing is a two-line fragment."""
    marks = _boundaries(lines, lang)
    spans = [(a, b) for a, b in zip(marks, marks[1:] + [len(lines)]) if b > a]

    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and (end - merged[-1][0]) <= MAX_CHUNK_LINES and (end - start) < MIN_CHUNK_LINES:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    # Anything still oversized (a 900-line class, or a language with no boundary regex)
    # falls back to overlapping windows so no chunk exceeds the budget.
    out: list[tuple[int, int]] = []
    for start, end in merged:
        if end - start <= MAX_CHUNK_LINES:
            out.append((start, end))
            continue
        step = MAX_CHUNK_LINES - WINDOW_OVERLAP
        for w in range(start, end, step):
            out.append((w, min(w + MAX_CHUNK_LINES, end)))
            if w + MAX_CHUNK_LINES >= end:
                break
    return out


_SYMBOL_RE = re.compile(r"\b(?:def|class|func|function|type|struct|impl|trait|interface)\s+(\w+)")


def chunk_file(path: str, text: str) -> list[Chunk]:
    lang = LANGS.get(Path(path).suffix.lower(), "text")
    lines = text.splitlines()
    chunks = []
    for start, end in _sections(lines, lang):
        body = "\n".join(lines[start:end]).strip("\n")
        if not body.strip():
            continue
        chunks.append(
            Chunk(
                path=path,
                start_line=start + 1,
                end_line=end,
                lang=lang,
                text=body,
                symbols=" ".join(sorted(set(_SYMBOL_RE.findall(body))))[:500],
            )
        )
    return chunks


def chunk_repo(repo: Path, max_files: int = 4000) -> list[Chunk]:
    return [c for path, text in walk(repo, max_files) for c in chunk_file(path, text)]
