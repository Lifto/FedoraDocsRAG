# Fedora Docs RAG Database

A pre-built RAG (Retrieval-Augmented Generation) database of Fedora documentation,
ready for use with local AI assistants.

## What is this?

This repository provides a **database dump** containing vectorized Fedora documentation,
suitable for semantic search and RAG-powered Q&A. Built using [docs2db](https://github.com/rhel-lightspeed/docs2db).

### Features

- 🚀 **Ready to use** - Download the dump, restore it, and start querying
- 📚 **Comprehensive** - Includes Quick Docs, Sysadmin Guide, CoreOS, Silverblue, and more
- 🔄 **Regularly updated** - Rebuilt when upstream documentation changes
- 🔓 **Open source** - Same license as Fedora documentation

## Quick Start

### 1. Download the database dump

```bash
# Download the latest release
curl -LO https://github.com/Lifto/FedoraDocsRAG/releases/latest/download/fedora-docs.sql
```

### 2. Restore and query

```bash
# Restore the dump (starts PostgreSQL via Podman automatically)
uvx docs2db db-start
uvx docs2db db-restore fedora-docs.sql

# Query the database
uvx docs2db-api query "How do I install packages on Fedora?"
```

## Building from Source

If you want to build the database yourself:

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker or Podman
- Git

### Build

```bash
# Clone this repository
git clone https://github.com/Lifto/FedoraDocsRAG.git
cd FedoraDocsRAG

# Install dependencies and build
uv sync
uv run python build.py
```

The build script will:
1. Check for upstream changes (skips rebuild if nothing changed)
2. Clone all Fedora documentation repositories
3. Build them with Antora (in a container)
4. Ingest, chunk, and embed using docs2db
5. Create a database dump in `dist/fedora-docs.sql`

Use `--force` to bypass change detection and force a full rebuild.

## Documentation Sources

This database includes documentation from:

| Source | Description |
|--------|-------------|
| [Quick Docs](https://docs.fedoraproject.org/en-US/quick-docs/) | Common tasks and tutorials |
| [Sysadmin Guide](https://docs.fedoraproject.org/en-US/fedora-server/) | Server administration |
| [Release Notes](https://docs.fedoraproject.org/en-US/fedora/latest/release-notes/) | Version-specific changes |
| [CoreOS](https://docs.fedoraproject.org/en-US/fedora-coreos/) | Container-focused OS |
| [Silverblue](https://docs.fedoraproject.org/en-US/fedora-silverblue/) | Immutable desktop |
| [IoT](https://docs.fedoraproject.org/en-US/iot/) | Internet of Things |
| And more... | See `build.py` for full list |

## License

### Database Content (CC-BY-SA 4.0)

The **database dump** containing Fedora documentation is licensed under the
[Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/).

This is a derivative work of [Fedora Documentation](https://docs.fedoraproject.org/),
which is licensed under CC-BY-SA by the Fedora Project.

### Build Scripts (Apache 2.0)

The **build scripts and tooling** in this repository are licensed under the
[Apache License 2.0](LICENSE).

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## Related Projects

- [docs2db](https://github.com/rhel-lightspeed/docs2db) - The ingestion pipeline
- [docs2db-api](https://github.com/rhel-lightspeed/docs2db-api) - Query API for docs2db databases
- [Fedora Docs](https://docs.fedoraproject.org/) - The upstream documentation

---

*Powered by [Drella](https://github.com/drellabot/orchestrator)*
