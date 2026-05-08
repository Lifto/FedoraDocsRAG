#!/usr/bin/env python3
"""
Build Fedora Docs RAG Database.

This script:
1. Clones all Fedora documentation source repositories
2. Builds them using Antora (via Docker/Podman)
3. Extracts clean HTML content from Antora output
4. Starts a PostgreSQL database (via docs2db)
5. Ingests, chunks, and embeds the content
6. Loads into PostgreSQL and creates a database dump

Requirements:
    - Docker or Podman
    - Git

Usage:
    uv run python build.py
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

WORK_DIR = Path("build")
CONTENT_DIR = Path("docs2db_content")
OUTPUT_DIR = Path("dist")
ANTORA_IMAGE = "docker.io/antora/antora"

# Official Fedora Docs site repository (contains the Antora playbook)
FEDORA_DOCS_SITE_REPO = "https://gitlab.com/fedora/docs/docs-website/docs-fp-o.git"

# License to assign to all Fedora documentation
FEDORA_LICENSE = "CC-BY-SA 4.0"

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_REQUIRED_KEYS = {"schema_version", "content_repos", "content_hash", "site_repo_sha"}


# =============================================================================
# Utility Functions
# =============================================================================


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    #    result = subprocess.run(cmd, cwd=cwd, capture_output=False, text=True)

    if check and result.returncode != 0:
        print(f"    Error (exit {result.returncode}): {result.stderr[:500]}")
    return result


def check_prerequisites() -> tuple[str | None, list[str]]:
    """Check for required tools. Returns (container_cmd, missing_tools)."""
    missing = []

    # Check for container runtime (prefer podman)
    container_cmd = None
    for cmd in ["podman", "docker"]:
        if shutil.which(cmd):
            container_cmd = cmd
            break

    if not container_cmd:
        missing.append("podman or docker")

    # Check for git
    if not shutil.which("git"):
        missing.append("git")

    return container_cmd, missing


def _detect_github_repo() -> str | None:
    """Detect the GitHub owner/repo from environment or git remote."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            if "github.com" in url:
                # https://github.com/owner/repo.git or git@github.com:owner/repo.git
                return url.rstrip("/").removesuffix(".git").split("github.com")[-1].lstrip("/:")
    except Exception:
        pass

    return None


def load_manifest() -> dict | None:
    """Download manifest from latest GitHub release; returns None on any failure (triggers full rebuild)."""
    repo = _detect_github_repo()
    if not repo:
        print("[manifest] Cannot determine GitHub repository. Rebuilding.")
        return None

    url = f"https://github.com/{repo}/releases/latest/download/manifest.json"
    tmp_path = Path("/tmp/manifest.json")
    try:
        urllib.request.urlretrieve(url, tmp_path)

        manifest = json.loads(tmp_path.read_text())

        missing = MANIFEST_REQUIRED_KEYS - manifest.keys()
        if missing:
            print(f"[manifest] Invalid manifest — missing keys: {missing}. Rebuilding.")
            return None

        if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
            print(
                f"[manifest] Schema version mismatch (got {manifest['schema_version']}, expected {MANIFEST_SCHEMA_VERSION}). Rebuilding."
            )
            return None

        print(
            f"[manifest] Loaded manifest from {manifest.get('build_date', 'unknown date')}, {len(manifest['content_repos'])} repos"
        )
        return manifest

    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[manifest] No prior manifest (no releases found). Rebuilding.")
        else:
            print(f"[manifest] HTTP {e.code} downloading manifest. Rebuilding.")
        return None
    except urllib.error.URLError as e:
        print(f"[manifest] Network error downloading manifest: {e.reason}. Rebuilding.")
        return None
    except json.JSONDecodeError as e:
        print(f"[manifest] Invalid JSON in manifest: {e}. Rebuilding.")
        return None
    except Exception as e:
        print(f"[manifest] Unexpected error loading manifest: {e}. Rebuilding.")
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def get_repo_head_sha(repo_dir: Path) -> str | None:
    """Return the HEAD commit SHA of a cloned git repository.

    Args:
        repo_dir: Path to the cloned repository directory.

    Returns:
        The HEAD commit SHA as a hex string, or None if the SHA cannot be
        determined (e.g. git is unavailable or the path is not a repository).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        print(f"[manifest] Warning: could not get SHA for {repo_dir}: {e}")
    return None


def get_site_repo_sha(site_dir: Path) -> str | None:
    """Return the HEAD commit SHA of the cloned site repository.

    Thin wrapper around :func:`get_repo_head_sha` for the site repo.

    Args:
        site_dir: Path to the cloned site repository directory.

    Returns:
        The HEAD commit SHA as a hex string, or None if unavailable.
    """
    return get_repo_head_sha(site_dir)


def check_repos_changed(manifest: dict, site_sha: str | None, repo_urls: list[str]) -> bool:
    """Gate 1: URL set diff; Gate 2: ls-remote SHA diff. Returns True if rebuild needed; True on any error (fail-safe)."""
    manifest_repos = set(manifest.get("content_repos", {}).keys())
    current_repos = set(repo_urls)

    added = current_repos - manifest_repos
    removed = manifest_repos - current_repos
    if added or removed:
        if added:
            print(f"[gate1] New repos detected: {added}")
        if removed:
            print(f"[gate1] Removed repos: {removed}")
        return True
    print(f"[gate1] Repo set unchanged ({len(current_repos)} repos)")

    if site_sha and manifest.get("site_repo_sha") != site_sha:
        print(f"[gate2] Site repo changed: {manifest.get('site_repo_sha')[:8]} -> {site_sha[:8]}")
        return True

    changed_repos = []
    for url in repo_urls:
        manifest_sha = manifest["content_repos"].get(url, "")
        try:
            result = subprocess.run(
                ["git", "ls-remote", url, "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                print(
                    f"[gate2] Warning: ls-remote failed for {url} (exit {result.returncode}). Treating as changed."
                )
                return True

            lines = result.stdout.strip().splitlines()
            if not lines:
                print(f"[gate2] Warning: no HEAD ref for {url}. Treating as changed.")
                return True

            current_sha = lines[0].split()[0]
            if current_sha != manifest_sha:
                changed_repos.append(url)

        except subprocess.TimeoutExpired:
            print(f"[gate2] Timeout for {url}. Treating as changed.")
            return True
        except Exception as e:
            print(f"[gate2] Error checking {url}: {e}. Treating as changed.")
            return True

    if changed_repos:
        print(f"[gate2] {len(changed_repos)} repos changed:")
        for url in changed_repos[:5]:
            print(f"  - {url}")
        if len(changed_repos) > 5:
            print(f"  ... and {len(changed_repos) - 5} more")
        return True

    print(f"[gate2] All {len(repo_urls)} repo SHAs unchanged")
    return False


def save_manifest(
    site_sha: str | None,
    repos_shas: dict[str, str],
    content_hash: str,
    pages_count: int,
) -> None:
    """Save the build manifest to dist/manifest.json.

    Writes a JSON file that records the current build state so that
    subsequent runs can detect whether a rebuild is necessary.

    Args:
        site_sha: HEAD SHA of the site repository, or None if unavailable.
        repos_shas: Mapping of content repo URL to its HEAD commit SHA.
        content_hash: xxHash digest of extracted HTML content (prefix
            ``"xxh64:<hex>"``), or empty string if content was unavailable.
        pages_count: Number of HTML pages extracted during this build.
    """
    import datetime

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "build_date": datetime.date.today().isoformat(),
        "site_repo_sha": site_sha or "",
        "content_repos": repos_shas,
        "content_hash": content_hash,
        "pages_extracted": pages_count,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(
        f"[manifest] Saved manifest: {pages_count} pages, {len(repos_shas)} repos -> {manifest_path}"
    )


def compute_content_hash(content_dir: Path) -> str | None:
    """Compute a deterministic xxHash over all extracted HTML files.

    Globs only the root-level ``*.html`` files in *content_dir* (not
    subdirectories — those are docs2db artifacts, not source content).  Files
    are sorted by name so the result is stable across runs regardless of
    filesystem ordering.

    For each file the hash is updated with the filename (UTF-8 encoded)
    followed by the raw file bytes, so a rename without a content change is
    still detected.

    Args:
        content_dir: Path to the docs2db content directory (e.g. CONTENT_DIR).

    Returns:
        A hex-prefixed digest string like ``"xxh64:<hex>"`` if at least one
        HTML file is present, or ``None`` if the directory is empty or does not
        exist.
    """
    import xxhash

    if not content_dir.exists():
        return None

    files = sorted(content_dir.glob("*.html"), key=lambda f: f.name)
    if not files:
        return None

    h = xxhash.xxh64()
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())

    return f"xxh64:{h.hexdigest()}"


# =============================================================================
# Build Steps
# =============================================================================


def clone_site_repo(work_dir: Path) -> Path | None:
    """Clone the official Fedora docs site repo to get the Antora playbook."""
    site_dir = work_dir / "docs-fp-o"

    if site_dir.exists():
        print("    Updating docs-fp-o (prod branch)...")
        result = run(["git", "pull"], cwd=site_dir, check=False)
    else:
        print("    Cloning docs-fp-o (prod branch)...")
        # site.yml is in the 'prod' branch, not main
        result = run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "prod",
                FEDORA_DOCS_SITE_REPO,
                str(site_dir),
            ],
            check=False,
        )

    if result.returncode != 0:
        print("    ❌ Failed to clone Fedora docs site repo")
        return None

    return site_dir


def create_simplified_site_yml(site_dir: Path) -> Path:
    """Create a simplified site.yml without custom extensions."""
    import yaml

    site_yml = site_dir / "site.yml"
    simplified_yml = site_dir / "site-simplified.yml"

    if not site_yml.exists():
        return site_yml

    with open(site_yml) as f:
        config = yaml.safe_load(f)

    # Remove custom extensions that require extra npm packages
    if "antora" in config:
        config["antora"].pop("extensions", None)

    # Remove asciidoc extensions that require extra npm packages
    if "asciidoc" in config:
        config["asciidoc"].pop("extensions", None)

    # Write simplified config to new file
    with open(simplified_yml, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print("  Created site-simplified.yml (removed custom extensions)")
    return simplified_yml


def extract_repos_from_site(site_dir: Path) -> list[str]:
    """Extract content source URLs from the Antora site.yml playbook."""
    import yaml

    site_yml = site_dir / "site.yml"
    if not site_yml.exists():
        print(f"  Error: site.yml not found in {site_dir}")
        return []

    try:
        with open(site_yml) as f:
            site_config = yaml.safe_load(f)
    except Exception as e:
        print(f"  Error parsing site.yml: {e}")
        return []

    urls = []
    sources = site_config.get("content", {}).get("sources", [])

    for source in sources:
        url = source.get("url", "")
        if url and url.startswith(("https://", "http://", "git@")):
            # Normalize URL
            if not url.endswith(".git"):
                url = url + ".git"
            urls.append(url)

    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    print(f"  Found {len(unique_urls)} unique content sources in site.yml")
    return unique_urls


def clone_repos(repos: list[str], work_dir: Path) -> list[Path]:
    """Clone or update repositories. Returns list of successfully cloned/updated paths."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cloned = []
    failed = []

    for url in repos:
        # Use org/repo format to avoid name clashes (e.g., atomic-desktops_docs)
        parts = url.rstrip("/").replace(".git", "").split("/")
        name = f"{parts[-2]}_{parts[-1]}" if len(parts) >= 2 else parts[-1]
        repo_dir = work_dir / name

        if repo_dir.exists():
            print(f"    Updating {name}...")
            result = run(["git", "pull"], cwd=repo_dir, check=False)
            if result.returncode == 0:
                cloned.append(repo_dir)
            else:
                print(f"    Failed to update {name}, using existing")
                cloned.append(repo_dir)
            continue

        print(f"    Cloning {name}...")
        result = run(["git", "clone", "--depth", "1", url, str(repo_dir)], check=False)

        if result.returncode == 0:
            cloned.append(repo_dir)
        else:
            print(f"    ❌ Failed to clone {name}")
            failed.append(name)

    print(f"\n  Clone summary: {len(cloned)}/{len(repos)} successful")
    if failed:
        print(f"  Failed repos: {', '.join(failed)}")

    return cloned


def create_antora_playbook(work_dir: Path, repo_dirs: list[Path]) -> bool:
    """Create a combined Antora playbook for all repos."""
    sources = []
    source_details = []  # Track which repos contribute sources
    repos_without_antora = []

    for repo_dir in repo_dirs:
        repo_has_antora = False  # Track if repo has ANY antora.yml (used or skipped)
        repo_sources = 0

        # Check if repo has antora.yml at root
        antora_yml = repo_dir / "antora.yml"
        if antora_yml.exists():
            repo_has_antora = True
            sources.append(f"    - url: ./{repo_dir.name}\n      branches: HEAD")
            source_details.append(f"{repo_dir.name} (root)")
            repo_sources += 1

        # Check for antora.yml in subdirectories
        for subdir in repo_dir.iterdir():
            antora_yml = subdir / "antora.yml"
            if subdir.is_dir() and antora_yml.exists():
                repo_has_antora = True
                sources.append(
                    f"    - url: ./{repo_dir.name}\n"
                    f"      start_path: {subdir.name}\n"
                    f"      branches: HEAD"
                )
                source_details.append(f"{repo_dir.name}/{subdir.name}")
                repo_sources += 1

        if not repo_has_antora:
            repos_without_antora.append(repo_dir.name)

    # Print detailed source info
    print(f"\n  Antora sources found ({len(sources)} total):")
    for detail in source_details:
        print(f"    ✓ {detail}")

    if repos_without_antora:
        print(f"\n  Repos without antora.yml ({len(repos_without_antora)}):")
        for name in repos_without_antora:
            print(f"    ⚠ {name}")

    if not sources:
        print("  Error: No valid Antora sources found!")
        return False

    playbook = f"""site:
  title: Fedora Documentation
  start_page: quick-docs::index.adoc
content:
  sources:
{chr(10).join(sources)}
ui:
  bundle:
    url: https://gitlab.com/fedora/docs/docs-website/ui-bundle/-/jobs/artifacts/HEAD/raw/build/ui-bundle.zip?job=bundle-stable
    snapshot: true
output:
  clean: true
  dir: ./public
runtime:
  fetch: true
"""

    playbook_path = work_dir / "site.yml"
    playbook_path.write_text(playbook)
    print(f"\n  Created {playbook_path}")
    return True


def build_with_antora(container_cmd: str, work_dir: Path, site_yml: str = "site.yml") -> bool:
    """Build documentation using Antora in a container."""
    cmd = [
        container_cmd,
        "run",
        "--rm",
        "-v",
        f"{work_dir.absolute()}:/antora:Z",
        ANTORA_IMAGE,
        site_yml,
    ]

    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error (exit {result.returncode}):")
        if result.stdout:
            print(f"  stdout: {result.stdout[:1000]}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:1000]}")
        return False

    print("  Antora build complete!")
    return True


def extract_html_content(work_dir: Path, output_dir: Path) -> int:
    """Extract article content from built HTML files."""
    import json
    from collections import defaultdict

    from bs4 import BeautifulSoup

    public_dir = work_dir / "public"
    if not public_dir.exists():
        print(f"  Error: Build output not found at {public_dir}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clear previous content
    for old_file in output_dir.glob("*.html"):
        old_file.unlink()
    for old_file in output_dir.glob("*.meta.json"):
        old_file.unlink()

    count = 0
    skipped_no_article = 0
    component_counts = defaultdict(int)  # Track pages per component

    for html_file in public_dir.rglob("*.html"):
        # Skip special files
        if html_file.name in ("404.html", "sitemap.html", "search.html"):
            continue

        try:
            soup = BeautifulSoup(html_file.read_text(errors="ignore"), "html.parser")

            # Extract main article content
            article = soup.find("article", class_="doc")
            if not article:
                article = soup.find("article")
            if not article:
                skipped_no_article += 1
                continue

            # Remove navigation elements
            for elem in article.find_all(["aside", "nav", "script"]):
                elem.decompose()

            # Get title
            title_elem = soup.find("title")
            title = title_elem.get_text(strip=True) if title_elem else html_file.stem

            # Create output filename
            rel_path = html_file.relative_to(public_dir)
            out_name = str(rel_path).replace("/", "_")
            out_path = output_dir / out_name

            # Track by component (first directory in path)
            component = rel_path.parts[0] if rel_path.parts else "unknown"
            component_counts[component] += 1

            # Write HTML with title
            content = f"<html><head><title>{title}</title></head><body>{article}</body></html>"
            out_path.write_text(content, encoding="utf-8")

            # Write metadata
            meta_path = output_dir / f"{out_name}.meta.json"
            meta = {
                "title": title,
                "source_url": f"https://docs.fedoraproject.org/{rel_path}",
                "license": FEDORA_LICENSE,
            }
            meta_path.write_text(json.dumps(meta, indent=2))

            count += 1

        except Exception as e:
            print(f"  Warning: Could not process {html_file}: {e}")

    # Print extraction summary
    print("\n  Pages extracted by component:")
    for component, comp_count in sorted(component_counts.items(), key=lambda x: -x[1]):
        print(f"    {component}: {comp_count} pages")

    if skipped_no_article:
        print(f"\n  Skipped {skipped_no_article} files (no article content)")

    return count


def run_docs2db_db_destroy() -> bool:
    """Run docs2db db-destroy to ensure clean state."""
    cmd = ["uv", "run", "docs2db", "db-destroy"]
    run(cmd, check=False)
    return True


def run_docs2db_db_start() -> bool:
    """Run docs2db db-start to start the database."""
    cmd = ["uv", "run", "docs2db", "db-start"]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_ingest(content_dir: Path) -> bool:
    """Run docs2db ingest on the extracted content."""
    cmd = ["uv", "run", "docs2db", "ingest", str(content_dir)]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_chunk() -> bool:
    """Run docs2db chunk (without contextual chunking - no LLM required)."""
    cmd = ["uv", "run", "docs2db", "chunk", "--skip-context"]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_embed() -> bool:
    """Run docs2db embed."""
    cmd = ["uv", "run", "docs2db", "embed", "--workers", "1"]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_config_refinement() -> bool:
    """Configure RAG refinement prompt for Fedora documentation."""
    prompt = """You are an expert in Fedora Linux, the Fedora Project, and Linux in general.

Your purpose is to generate meaningful and specific questions based on user queries.

You will receive a user query which is potentially unclear, incomplete or ambiguous.

Your role is to generate five user-simulated questions that are more specific, focused, and free of ambiguity.

### YOUR AREAS OF EXPERTISE
You are knowledgeable about:
- Fedora Linux usage, installation, configuration, and administration
- Fedora variants: Workstation, Server, CoreOS, Silverblue, Kinoite, IoT, Cloud
- Package management: DNF, rpm-ostree, Flatpak
- Containers: Podman, Docker, Toolbox
- Fedora Project infrastructure, processes, and community
- Contributing to Fedora: packaging, documentation, QA, translations
- Linux system administration in general

### WORKFLOW PROTOCOL
**1. Validate the User Query**
You must consider the user query as invalid ONLY if it meets any of the following criteria:
- Is purely a greeting with no question (e.g., "hello", "hi there")
- Is complete nonsense or gibberish
- Conflicts with ethical, legal, or moral principles

If the user query is invalid:
- Your response must only be the string "EMPTY" and nothing else.

If the user query could reasonably relate to Fedora, Linux, or open source, proceed with generating questions.

**2. Generate the Questions**
- Each of the new questions must derive from the original query.
- Compose them as if you were the user asking an expert about Fedora or Linux.
- Assume the user is running a recent version of Fedora Linux (Fedora 40+).

**Response Format and Structure**
- Your response must contain five questions in total.
- Each question must be on its own line using plain text.
- Avoid wrapping the questions with quotes or double quotes.
- Avoid numbered lists, bullet points, headings, or any formatting. Just plain text.
- DO NOT include any introduction, explanation, commentary, or conclusion.

User query: {question}"""

    cmd = ["uv", "run", "docs2db", "config", "--refinement-prompt", prompt]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_load(title: str, description: str) -> bool:
    """Run docs2db load to insert data into the database."""
    cmd = [
        "uv",
        "run",
        "docs2db",
        "load",
        "--title",
        title,
        "--description",
        description,
    ]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_db_dump(output_file: Path) -> bool:
    """Run docs2db db-dump to create the database dump."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["uv", "run", "docs2db", "db-dump", "--output-file", str(output_file)]
    result = run(cmd, check=False)
    return result.returncode == 0


def run_docs2db_db_stop() -> bool:
    """Run docs2db db-stop to stop the database."""
    cmd = ["uv", "run", "docs2db", "db-stop"]
    result = run(cmd, check=False)
    return result.returncode == 0


def cleanup(work_dir: Path) -> None:
    """Remove build directory."""
    if work_dir.exists():
        print(f"  Removing {work_dir}/...")
        shutil.rmtree(work_dir)


def cleanup_orphaned_docs(content_dir: Path) -> int:
    """Remove stale docs2db artifacts that no longer have a matching HTML file.

    After extract_html_content() writes fresh .html files, any subdirectory or
    .html.meta.json file in content_dir whose name does not correspond to an
    existing .html file is considered orphaned (its source page was removed from
    the Fedora docs site). This prevents stale content from being re-ingested on
    subsequent runs.

    Args:
        content_dir: Path to the docs2db content directory (e.g. CONTENT_DIR).

    Returns:
        Number of orphaned items (directories + meta files) removed.
    """
    if not content_dir.exists():
        return 0

    removed = 0
    for item in content_dir.iterdir():
        if item.is_dir():
            corresponding_html = content_dir / f"{item.name}.html"
            if not corresponding_html.exists():
                print(f"  Removing orphaned docs2db directory: {item.name}/")
                shutil.rmtree(item)
                removed += 1
        elif item.name.endswith(".html.meta.json"):
            # Remove orphaned meta files whose source .html no longer exists
            stem = item.name[: -len(".meta.json")]  # e.g. "foo.html"
            if not (content_dir / stem).exists():
                print(f"  Removing orphaned meta file: {item.name}")
                item.unlink()
                removed += 1

    print(f"  Removed {removed} orphaned docs2db item{'s' if removed != 1 else ''}")
    return removed


# =============================================================================
# Main Entry Point
# =============================================================================


def main(args) -> int:
    print("=" * 70)
    print("Fedora Docs RAG Database Builder")
    print("=" * 70)
    print()

    # Check prerequisites
    container_cmd, missing = check_prerequisites()
    if missing:
        print(f"Error: Missing required tools: {', '.join(missing)}")
        return 1
    print(f"Container runtime: {container_cmd}")

    steps_total = 13

    # Step 1: Clone Fedora docs site repo (to get the list of content sources)
    print(f"\n[1/{steps_total}] Fetching Fedora docs site configuration...")
    site_dir = clone_site_repo(WORK_DIR)
    if not site_dir:
        print("Error: Could not fetch Fedora docs site repo!")
        return 1
    site_sha = get_site_repo_sha(site_dir)

    # Step 2: Extract and clone content repositories
    repos = extract_repos_from_site(site_dir)

    if not args.force:
        manifest = load_manifest()
        if manifest is not None:
            if not check_repos_changed(manifest, site_sha, repos):
                print("\nNo changes detected. Skipping build.")
                return 0
        else:
            print("[manifest] No prior manifest. Proceeding with full build.")
    else:
        print("[manifest] --force flag set. Skipping change detection.")
        manifest = None

    print(f"\n[2/{steps_total}] Cloning {len(repos)} content repositories...")
    repo_dirs = clone_repos(repos, WORK_DIR)
    if not repo_dirs:
        print("Error: No repositories cloned!")
        return 1
    print(f"  Cloned {len(repo_dirs)} repositories")

    repos_shas = {}
    for url in repos:
        parts = url.rstrip("/").replace(".git", "").split("/")
        name = f"{parts[-2]}_{parts[-1]}" if len(parts) >= 2 else parts[-1]
        sha = get_repo_head_sha(WORK_DIR / name)
        if sha:
            repos_shas[url] = sha

    # Step 3: Create Antora playbook with local paths
    print(f"\n[3/{steps_total}] Creating Antora playbook...")
    if not create_antora_playbook(WORK_DIR, repo_dirs):
        print("  (build/ directory preserved for debugging)")
        return 1

    # Step 4: Build with Antora
    print(f"\n[4/{steps_total}] Building with Antora (this may take several minutes)...")
    if not build_with_antora(container_cmd, WORK_DIR):
        print("  (build/ directory preserved for debugging)")
        return 1

    # Step 5: Extract HTML content
    print(f"\n[5/{steps_total}] Extracting HTML content...")
    count = extract_html_content(WORK_DIR, CONTENT_DIR)
    if count == 0:
        print("Error: No content extracted!")
        print("  (build/ directory preserved for debugging)")
        return 1
    print(f"  Extracted {count} pages")

    # Step 5.5: Remove orphaned docs2db artifact directories
    print(f"\n[5.5/{steps_total}] Removing orphaned docs2db artifacts...")
    cleanup_orphaned_docs(CONTENT_DIR)

    # Remove Antora output only — preserve git clones for faster subsequent runs
    shutil.rmtree(WORK_DIR / "public", ignore_errors=True)
    print(f"  Removed Antora output at {WORK_DIR / 'public'}")

    # Gate 3: Content hash check (false-positive prevention)
    content_hash = compute_content_hash(CONTENT_DIR)
    if (
        manifest is not None
        and content_hash is not None
        and manifest.get("content_hash") == content_hash
    ):
        print("Content hash unchanged despite SHA changes — skipping rebuild")
        return 0

    # Step 6: Ingest with docs2db
    print(f"\n[6/{steps_total}] Ingesting with docs2db...")
    if not run_docs2db_ingest(CONTENT_DIR):
        print("Warning: Ingest may have had issues, continuing...")

    # Step 7: Chunk
    print(f"\n[7/{steps_total}] Chunking documents...")
    if not run_docs2db_chunk():
        print("Error: Chunking failed!")
        return 1

    # Step 8: Embed
    print(f"\n[8/{steps_total}] Generating embeddings...")
    if not run_docs2db_embed():
        print("Error: Embedding failed!")
        return 1

    # Step 9: Destroy existing database (ensure clean state)
    print(f"\n[9/{steps_total}] Destroying existing database (if any)...")
    run_docs2db_db_destroy()

    # Step 10: Start database
    print(f"\n[10/{steps_total}] Starting database...")
    if not run_docs2db_db_start():
        print("Error: Failed to start database!")
        return 1

    print("  Waiting for PostgreSQL to initialize...")
    time.sleep(5)

    # Step 11: Configure refinement prompt
    print(f"\n[11/{steps_total}] Configuring RAG refinement prompt...")
    if not run_docs2db_config_refinement():
        print("Warning: Could not set refinement prompt, continuing...")

    # Step 12: Load into database
    print(f"\n[12/{steps_total}] Loading into database...")
    if not run_docs2db_load(
        title="Fedora Documentation",
        description="RAG database of Fedora Project documentation generated by https://github.com/Lifto/FedoraDocsRAG",
    ):
        print("Error: Loading failed!")
        run_docs2db_db_stop()
        return 1

    # Step 13: Create database dump
    print(f"\n[13/{steps_total}] Creating database dump...")
    dump_file = OUTPUT_DIR / "fedora-docs.sql"
    if not run_docs2db_db_dump(dump_file):
        print("Error: Dump creation failed!")
        run_docs2db_db_stop()
        return 1

    save_manifest(site_sha, repos_shas, content_hash or "", count)

    # Stop database
    print("\nStopping database...")
    run_docs2db_db_stop()

    print()
    print("=" * 70)
    print("Build complete!")
    print()
    print(f"Database dump: {dump_file}")
    print()
    print("The dump is ready for distribution!")
    print("Users can restore with: docs2db db-restore fedora-docs.sql")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build FedoraDocsRAG")
    parser.add_argument(
        "--force", action="store_true", help="Force full rebuild, ignoring manifest"
    )
    args = parser.parse_args()
    sys.exit(main(args))
