# Changelog

All notable changes to FedoraDocsRAG will be documented in this file.

## [1.3.0] - 2026-05-08

### Added
- Change detection via `git ls-remote` SHA comparison — skips full rebuild when upstream docs haven't changed
- Content hash gate (xxHash) as false-positive safety net — prevents rebuild when SHAs change but actual content doesn't
- Manifest file (`dist/manifest.json`) published with each release, tracking repo SHAs and content hash
- `--force` flag to bypass change detection and force a full rebuild
- Force-rebuild workflow input for `workflow_dispatch` triggers
- Retry with backoff (3s/6s) for flaky `git ls-remote` calls

### Fixed
- Data loss: duplicate component check was silently dropping sysadmin-guide (45+ pages)
- Manifest not saved on skip path, causing redundant rebuilds every run (groundhog day bug)
- Unreachable repos triggering false-positive rebuilds indefinitely
- Replaced `gh` CLI with `urllib` for manifest download — no `GH_TOKEN` needed in CI

### Changed
- CI rebuild schedule changed from weekly (Monday) to daily at 06:00 UTC

## [1.2.0] - 2026-05-08

### Added
- Incremental rebuild: orphaned docs2db artifact directories are automatically removed when their source HTML is no longer extracted, preventing stale content from appearing in the database
- Persistent git clones: the Fedora doc repositories in `build/` are preserved between runs, enabling `git pull` instead of full re-clone on subsequent builds
- Automated weekly CI: GitHub Actions workflow rebuilds the documentation database every Monday and publishes a fresh `fedora-docs.sql` as a date-versioned GitHub Release

## [1.1.1] - 2026-01-19

### Fixed

- Fixed README quick start example

## [1.1.0] - 2026-01-16

### Improved

- Database includes Fedora Docs specific refinement prompt.

## [1.0.0] - 2026-01-15

### Initial Release

- Complete RAG database of Fedora Project documentation
- **1,681 pages** extracted from **64 repositories**
- Dynamically fetches repository list from official Fedora docs site configuration
- Handles duplicate Antora components automatically
- Includes documentation from:
  - Quick Docs, Install Guide, Release Notes
  - Fedora CoreOS, Silverblue, Kinoite, Atomic Desktops
  - Server, IoT, Cloud, Workstation
  - EPEL, Packaging Guidelines, Infrastructure
  - Community guides, Legal, Marketing, and more

### Technical Details

- Built with [docs2db](https://github.com/rhel-lightspeed/docs2db)
- Uses Antora for documentation rendering
- Embedding model: `ibm-granite/granite-embedding-30m-english`
- Database: PostgreSQL with pgvector
