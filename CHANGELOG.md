# Changelog

All notable changes to FedoraDocsRAG will be documented in this file.

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
