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

import shutil
import subprocess
import sys
import time
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

WORK_DIR = Path("build")
CONTENT_DIR = Path("docs2db_content")
OUTPUT_DIR = Path("dist")
ANTORA_IMAGE = "docker.io/antora/antora"

# All Fedora documentation repositories
REPOS = [
    # Core documentation
    "https://pagure.io/fedora-docs/quick-docs.git",
    "https://gitlab.com/fedora/docs/fedora-linux-documentation/fedora-linux-sysadmin-guide.git",
    "https://gitlab.com/fedora/docs/fedora-linux-documentation/release-notes.git",
    "https://gitlab.com/fedora/docs/fedora-linux-documentation/release-docs-home.git",
    # Container/Cloud variants
    "https://github.com/coreos/fedora-coreos-docs.git",
    "https://github.com/containers/podman.io.git",
    "https://pagure.io/atomic-desktops/docs.git",
    "https://github.com/fedora-silverblue/silverblue-docs.git",
    # Server/Infrastructure
    "https://pagure.io/fedora-docs/fedora-server-docs.git",
    "https://pagure.io/epel/epel-docs.git",
    "https://pagure.io/fedora-infra/infra-docs.git",
    # IoT
    "https://github.com/fedora-iot/iot-docs.git",
    # Community/Contributor
    "https://gitlab.com/fedora/docs/community-tools/documentation-contributors-guide.git",
    "https://gitlab.com/fedora/mentoring/home.git",
    "https://pagure.io/fedora-join/fedora-join-docs.git",
    # Packaging
    "https://pagure.io/fedora-docs/package-maintainer-docs.git",
    "https://pagure.io/fedora-docs/flatpak.git",
    # QA/CI
    "https://pagure.io/fedora-qa/qa-docs.git",
    "https://pagure.io/fedora-ci/docs.git",
]

# License to assign to all Fedora documentation
FEDORA_LICENSE = "CC-BY-SA 4.0"


# =============================================================================
# Utility Functions
# =============================================================================


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  $ {' '.join(cmd)}")
#    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    result = subprocess.run(cmd, cwd=cwd, capture_output=False, text=True)

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


# =============================================================================
# Build Steps
# =============================================================================


def clone_repos(repos: list[str], work_dir: Path) -> list[Path]:
    """Clone or update repositories. Returns list of successfully cloned/updated paths."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cloned = []

    for url in repos:
        name = url.rstrip("/").split("/")[-1].replace(".git", "")
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
            print(f"    Failed to clone {name}")

    return cloned


def create_antora_playbook(work_dir: Path, repo_dirs: list[Path]) -> bool:
    """Create a combined Antora playbook for all repos."""
    sources = []

    for repo_dir in repo_dirs:
        # Check if repo has antora.yml at root
        if (repo_dir / "antora.yml").exists():
            sources.append(f"    - url: ./{repo_dir.name}\n      branches: HEAD")

        # Check for antora.yml in subdirectories
        for subdir in repo_dir.iterdir():
            if subdir.is_dir() and (subdir / "antora.yml").exists():
                sources.append(
                    f"    - url: ./{repo_dir.name}\n"
                    f"      start_path: {subdir.name}\n"
                    f"      branches: HEAD"
                )

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
    print(f"  Created {playbook_path} with {len(sources)} sources")
    return True


def build_with_antora(container_cmd: str, work_dir: Path) -> bool:
    """Build documentation using Antora in a container."""
    cmd = [
        container_cmd, "run", "--rm",
        "-v", f"{work_dir.absolute()}:/antora:Z",
        ANTORA_IMAGE,
        "site.yml"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Error: {result.stderr[:500]}")
        return False

    print("  Antora build complete!")
    return True


def extract_html_content(work_dir: Path, output_dir: Path) -> int:
    """Extract article content from built HTML files."""
    import json

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

    return count


def run_docs2db_db_destroy() -> bool:
    """Run docs2db db-destroy to ensure clean state."""
    cmd = ["uv", "run", "docs2db", "db-destroy"]
    result = run(cmd, check=False)
    # db-destroy may fail if no database exists, that's OK
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


def run_docs2db_load(title: str, description: str) -> bool:
    """Run docs2db load to insert data into the database."""
    cmd = [
        "uv", "run", "docs2db", "load",
        "--title", title,
        "--description", description,
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


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> int:
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

    steps_total = 12

    # Step 1: Clone repositories
    print(f"\n[1/{steps_total}] Cloning {len(REPOS)} repositories...")
    repo_dirs = clone_repos(REPOS, WORK_DIR)
    if not repo_dirs:
        print("Error: No repositories cloned!")
        return 1
    print(f"  Cloned {len(repo_dirs)} repositories")

    # Step 2: Create Antora playbook
    print(f"\n[2/{steps_total}] Creating Antora playbook...")
    if not create_antora_playbook(WORK_DIR, repo_dirs):
        cleanup(WORK_DIR)
        return 1

    # Step 3: Build with Antora
    print(f"\n[3/{steps_total}] Building with Antora (this may take several minutes)...")
    if not build_with_antora(container_cmd, WORK_DIR):
        cleanup(WORK_DIR)
        return 1

    # Step 4: Extract HTML content
    print(f"\n[4/{steps_total}] Extracting HTML content...")
    count = extract_html_content(WORK_DIR, CONTENT_DIR)
    if count == 0:
        print("Error: No content extracted!")
        cleanup(WORK_DIR)
        return 1
    print(f"  Extracted {count} pages")

    # Clean up build directory
    cleanup(WORK_DIR)

    # Step 5: Ingest with docs2db
    print(f"\n[5/{steps_total}] Ingesting with docs2db...")
    if not run_docs2db_ingest(CONTENT_DIR):
        print("Warning: Ingest may have had issues, continuing...")

    # Step 6: Chunk
    print(f"\n[6/{steps_total}] Chunking documents...")
    if not run_docs2db_chunk():
        print("Error: Chunking failed!")
        return 1

    # Step 7: Embed
    print(f"\n[7/{steps_total}] Generating embeddings...")
    if not run_docs2db_embed():
        print("Error: Embedding failed!")
        return 1

    # Step 8: Destroy existing database (ensure clean state)
    print(f"\n[8/{steps_total}] Destroying existing database (if any)...")
    run_docs2db_db_destroy()

    # Step 9: Start database
    print(f"\n[9/{steps_total}] Starting database...")
    if not run_docs2db_db_start():
        print("Error: Failed to start database!")
        return 1
    
    print("  Waiting for PostgreSQL to initialize...")
    time.sleep(5)

    # Step 10: Load into database
    print(f"\n[10/{steps_total}] Loading into database...")
    if not run_docs2db_load(
        title="Fedora Documentation",
        description="RAG database of Fedora Project documentation generated by https://github.com/Lifto/FedoraDocsRAG"
    ):
        print("Error: Loading failed!")
        run_docs2db_db_stop()
        return 1

    # Step 11: Create database dump
    print(f"\n[11/{steps_total}] Creating database dump...")
    dump_file = OUTPUT_DIR / "fedora-docs.sql"
    if not run_docs2db_db_dump(dump_file):
        print("Error: Dump creation failed!")
        run_docs2db_db_stop()
        return 1

    # Step 12: Stop database
    print(f"\n[12/{steps_total}] Stopping database...")
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
    sys.exit(main())
