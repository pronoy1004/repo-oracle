"""Chunking decides what retrieval can ever find, so the boundaries are worth pinning."""

from pathlib import Path

from repo_oracle.chunk import MAX_CHUNK_LINES, chunk_file, walk

PY = """\
import os

CONST = 1


def alpha():
    total = 0
    for i in range(10):
        total += i
    if total > 5:
        total -= 1
    return total


def beta(x):
    \"\"\"Doc.\"\"\"
    y = x + 1
    z = y * 2
    for _ in range(3):
        z += 1
    return z


class Gamma:
    def method(self):
        value = 2
        for _ in range(4):
            value *= 2
        return value

    def other(self):
        value = 3
        for _ in range(4):
            value += 2
        return value
"""


def test_splits_on_declarations_not_arbitrary_windows():
    chunks = chunk_file("a.py", PY)
    starts = [c.text.splitlines()[0] for c in chunks]
    assert any(s.startswith("def beta") for s in starts)
    assert any(s.startswith("class Gamma") for s in starts)
    # A method must stay inside its class, not become its own chunk.
    gamma = next(c for c in chunks if c.text.startswith("class Gamma"))
    assert "def method" in gamma.text
    # No declaration is ever cut in half, whatever the merging did.
    assert all(c.text.count("def alpha") <= 1 for c in chunks)


def test_tiny_neighbours_merge_instead_of_becoming_useless_fragments():
    # A two-line function on its own retrieves badly: too little context to answer with.
    # Adjacent small declarations are merged up to the chunk ceiling instead.
    tiny = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    assert len(chunk_file("t.py", tiny)) == 1


def test_line_numbers_point_at_the_real_lines():
    lines = PY.splitlines()
    for chunk in chunk_file("a.py", PY):
        assert lines[chunk.start_line - 1] == chunk.text.splitlines()[0]
        assert chunk.end_line >= chunk.start_line


def test_oversized_sections_fall_back_to_bounded_windows():
    huge = "def one():\n" + "".join(f"    x = {i}\n" for i in range(500))
    chunks = chunk_file("big.py", huge)
    assert len(chunks) > 1
    assert all(c.end_line - c.start_line <= MAX_CHUNK_LINES for c in chunks)


def test_unknown_language_still_chunks():
    chunks = chunk_file("notes.xyz", "\n".join(f"line {i}" for i in range(300)))
    assert chunks and all(c.end_line - c.start_line <= MAX_CHUNK_LINES for c in chunks)


def test_embed_text_leads_with_the_location():
    chunk = chunk_file("a.py", PY)[0]
    assert chunk.embed_text().startswith("a.py:1-")


def test_walk_skips_junk_and_never_indexes_secret_contents(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("module.exports = 1\n")
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (tmp_path / ".env").write_text("SECRET_KEY=hunter2\n")

    found = dict(walk(tmp_path))
    assert "src/main.py" in found
    assert "node_modules/dep.js" not in found
    assert "package-lock.json" not in found
    assert "logo.png" not in found
    assert "hunter2" not in found[".env"]  # the name is context, the value is not ours
