"""Smoke tests for build.py change-detection logic."""

from build import check_repos_changed


def _manifest(repos: dict[str, str], site_sha: str = "abc123") -> dict:
    """Build a minimal manifest dict for testing."""
    return {
        "schema_version": 1,
        "content_repos": repos,
        "content_hash": "xxh64:deadbeef",
        "site_repo_sha": site_sha,
    }


def test_added_repo_triggers_rebuild():
    """Gate 1: new repo in current set that wasn't in manifest."""
    manifest = _manifest({"https://a.git": "sha1"})
    assert check_repos_changed(manifest, "abc123", ["https://a.git", "https://b.git"]) is True


def test_removed_repo_triggers_rebuild():
    """Gate 1: repo in manifest no longer in current set."""
    manifest = _manifest({"https://a.git": "sha1", "https://b.git": "sha2"})
    assert check_repos_changed(manifest, "abc123", ["https://a.git"]) is True


def test_site_sha_change_triggers_rebuild():
    """Gate 2: site repo HEAD changed since last build."""
    manifest = _manifest({"https://a.git": "sha1"}, site_sha="old_sha")
    assert check_repos_changed(manifest, "new_sha", ["https://a.git"]) is True
