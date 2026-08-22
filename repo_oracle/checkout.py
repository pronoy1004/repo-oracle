"""Get a repository onto disk for a run, safely.

Two ways in, both of which are attack surface if taken at face value:

- A git URL is fetched from the network. Anything but http(s) lets git run a local
  command or read a local path, so the scheme is checked before the clone.
- A local path is read off the host filesystem. Without an allowlist, a caller can point
  the agent at any directory the service can read, which is the whole disk.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# A git ref, not a shell fragment. Passed as an argv element, never through a shell,
# but a leading dash would still be read by git as an option.
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")

ALLOWED_ROOTS_ENV = "ALLOWED_REPO_ROOTS"


class CheckoutError(ValueError):
    """The caller asked for something this service will not fetch."""


def allowed_roots() -> list[Path]:
    raw = os.environ.get(ALLOWED_ROOTS_ENV, "")
    return [Path(p).expanduser().resolve() for p in raw.split(":") if p.strip()]


def resolve_local(path: str) -> Path:
    """Resolve a caller-supplied path, refusing anything outside the allowlist."""
    roots = allowed_roots()
    if not roots:
        raise CheckoutError(
            f"local paths are refused because {ALLOWED_ROOTS_ENV} is not set. "
            "Set it to a colon-separated list of directories this service may read, "
            "or send a git URL instead."
        )
    # Resolve first: this collapses .. and follows symlinks, so the check sees the
    # real destination rather than the string the caller wrote.
    target = Path(path).expanduser().resolve()
    if not target.is_dir():
        raise CheckoutError(f"not a directory: {target}")
    if not any(target == root or root in target.parents for root in roots):
        raise CheckoutError(f"path is outside {ALLOWED_ROOTS_ENV}: {target}")
    return target


def clone(url: str, ref: str | None = None) -> tuple[Path, callable]:
    """Shallow-clone `url` into a temp directory. Returns the path and a cleanup callable."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise CheckoutError(
            f"refusing scheme {parsed.scheme or '(none)'}; only http and https are allowed"
        )
    if not parsed.netloc:
        raise CheckoutError("git URL has no host")
    if ref is not None and not REF_RE.match(ref):
        raise CheckoutError(f"not a usable git ref: {ref!r}")

    tmp = Path(tempfile.mkdtemp(prefix="repo-oracle-"))
    dest = tmp / "repo"

    def cleanup() -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    cmd = ["git", "clone", "--depth", "1", "--no-tags"]
    if ref:
        cmd += ["--branch", ref]
    cmd += ["--", url, str(dest)]
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            # Never let the clone stop for credentials; a private URL should fail fast
            # rather than hang the run on a prompt nobody can answer.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
        )
    except subprocess.CalledProcessError as exc:
        cleanup()
        raise CheckoutError(f"git clone failed: {exc.stderr.strip()[:400]}") from exc
    except subprocess.TimeoutExpired:
        cleanup()
        raise CheckoutError("git clone timed out after 300s") from None
    return dest, cleanup


def head_sha(repo: Path) -> str | None:
    """The commit the map describes. A map with no version rots without anyone noticing."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
