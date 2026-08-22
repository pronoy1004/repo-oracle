"""The trust boundary. Everything here is a refusal that must stay a refusal."""

import pytest

from repo_oracle import checkout


def test_refuses_non_http_schemes():
    # file:// and ssh:// let git read local paths or run a local command.
    for url in ["file:///etc/passwd", "ssh://git@host/repo.git", "/etc", "git@github.com:a/b"]:
        with pytest.raises(checkout.CheckoutError):
            checkout.clone(url)


def test_refuses_a_ref_that_git_would_read_as_an_option():
    with pytest.raises(checkout.CheckoutError):
        checkout.clone("https://example.com/a/b", "--upload-pack=touch /tmp/pwned")


def test_local_paths_are_refused_unless_allowlisted(tmp_path, monkeypatch):
    monkeypatch.delenv(checkout.ALLOWED_ROOTS_ENV, raising=False)
    with pytest.raises(checkout.CheckoutError):
        checkout.resolve_local(str(tmp_path))

    monkeypatch.setenv(checkout.ALLOWED_ROOTS_ENV, str(tmp_path))
    assert checkout.resolve_local(str(tmp_path)) == tmp_path.resolve()

    outside = tmp_path.parent
    with pytest.raises(checkout.CheckoutError):
        checkout.resolve_local(str(outside))


def test_traversal_out_of_an_allowed_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv(checkout.ALLOWED_ROOTS_ENV, str(root))
    with pytest.raises(checkout.CheckoutError):
        checkout.resolve_local(str(root / ".." ))
