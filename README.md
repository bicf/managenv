# managenv

A tool to manage multiple `.env` files by combining reusable fragments into artifacts.

## Installation

```bash
pip install .
# or
uv pip install .
```

This makes the `managenv` command available. Alternatively, run directly with `python managenv.py`.

---

## Quick Start

**1. Create fragments in `fragments/`:**

```env
# fragments/backend.env
API_HOST=localhost
API_PORT=8000

# fragments/frontend.env
VITE_API_URL=http://localhost:8000
```

**2. Configure `managenv-config.json`:**

```json
{
  "fragments": {
    "backend": {"uri": "file://fragments/backend.env"},
    "frontend": {"uri": "file://fragments/frontend.env"}
  },
  "artifacts": {
    "dev.env": ["backend", "frontend"]
  }
}
```

**3. Generate:**

```bash
managenv
# Generated: artifacts/dev.env
```

---

## Commands

### Generate All Artifacts

```bash
managenv
```

Generates all artifacts defined in the config.

### Generate Specific Artifacts

```bash
managenv -a dev.env
```

Generates only the specified artifact.

**Multiple artifacts** (repeatable flag or comma-separated):
```bash
managenv -a dev.env -a prod.env
managenv dev.env,prod.env
```

### Dry Run (Preview)

```bash
managenv --dry-run
```

Shows what would be generated without writing files.

### List Configuration

```bash
managenv --list
```

Displays all fragments and artifacts defined in the config.

### Validate Configuration

```bash
managenv --validate
```

Checks for missing files and undefined fragment references.

### Show Diff

```bash
managenv --diff dev.env
```

Shows what would change compared to existing artifact files.

**Multiple artifacts**:
```bash
managenv --diff dev.env --diff prod.env
managenv --diff dev.env,prod.env
```

### Add Artifact

```bash
managenv --add dev.env backend,frontend
```

Adds a new artifact definition to the config. Optionally specify a custom deployment path:

```bash
managenv --add prod.env backend.prod --uri file:///var/www/app/.env
```

Use `--deploy` to generate the artifact immediately after adding:

```bash
managenv --add dev.env backend,frontend --deploy
```

### Delete Artifact

```bash
managenv --delete dev.env
```

Removes an artifact definition from the config.

### Import Existing Env File

```bash
managenv --import existing.env --prefix backend
```

Imports an existing `.env` file as a new fragment. Auto-creates config if needed.

### Initialize Config

```bash
managenv --init
```

Creates a new `managenv-config.json` with default structure.

### Shell Completion

**Save to file:**
```bash
# Generate bash completion
managenv --scripts bash > ~/.managenv-completion.bash
echo 'source ~/.managenv-completion.bash' >> ~/.bashrc

# Generate zsh completion
managenv --scripts zsh > ~/.managenv-completion.zsh
echo 'source ~/.managenv-completion.zsh' >> ~/.zshrc
```

**Load directly in current shell:**
```bash
# Bash
source <(./managenv.py --scripts bash)

# Zsh
source <(./managenv.py --scripts zsh)
```

Provides intelligent completion for artifact names, fragment names, and flags.

### Custom Config File

```bash
managenv -c /path/to/config.json
```

Uses a custom config file instead of the default `managenv-config.json` (located next to the script).

---

## Features

### Fragments

Fragments are small `.env` snippets that can be combined. Each fragment is defined with a URI.

**Config:**
```json
{
  "fragments": {
    "database": {"uri": "file://fragments/database.env"},
    "cache": {"uri": "file://fragments/cache.env"}
  }
}
```

**Fragment file (`fragments/database.env`):**
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=myapp
```

---

### Artifacts

Artifacts are the generated output files. They combine one or more fragments.

**Brief format** (array of fragment aliases):
```json
{
  "artifacts": {
    "dev.env": ["database", "cache"]
  }
}
```

This saves to `artifacts/dev.env` by default.

**Full format** (with custom deployment path):
```json
{
  "artifacts": {
    "dev.env": {
      "fragments": ["database", "cache"],
      "deployment": "file:///home/user/project/.env"
    }
  }
}
```

This saves to the specified absolute path.

---

### Auto-Inheritance (Dot Notation)

Use dot notation to create inheritance chains. When you reference `database.prod`, it automatically includes `database` first, then applies `database.prod` overrides.

**Config:**
```json
{
  "fragments": {
    "database": {"uri": "file://fragments/database.env"},
    "database.prod": {"uri": "file://fragments/database.prod.env"}
  },
  "artifacts": {
    "production.env": ["database.prod"]
  }
}
```

**Fragments:**
```env
# fragments/database.env
DB_HOST=localhost
DB_PORT=5432

# fragments/database.prod.env
DB_HOST=prod.rds.amazonaws.com
```

**Result (`artifacts/production.env`):**
```env
DB_HOST=prod.rds.amazonaws.com
DB_PORT=5432
```

The `database.prod` reference automatically includes `database` first, then overrides `DB_HOST`.

---

### URI Formats

Fragments can be loaded from various sources.

**Relative path** (both formats work):
```json
{"uri": "fragments/backend.env"}
{"uri": "file://fragments/backend.env"}
```

**Absolute path:**
```json
{"uri": "/etc/shared/common.env"}
{"uri": "file:///etc/shared/common.env"}
```

**URL (read-only):**
```json
{"uri": "https://config.example.com/base.env"}
```

URL content is cached during each run.

---

### Deployment Paths

Control where artifacts are saved using the `deployment` property.

**Default** (saves to `artifacts/` folder):
```json
{
  "artifacts": {
    "dev.env": ["backend", "frontend"]
  }
}
```
Saves to: `artifacts/dev.env`

**Relative path:**
```json
{
  "artifacts": {
    "dev.env": {
      "fragments": ["backend"],
      "deployment": "file://output/dev.env"
    }
  }
}
```
Saves to: `output/dev.env` (relative to config)

**Absolute path:**
```json
{
  "artifacts": {
    "production.env": {
      "fragments": ["backend.prod"],
      "deployment": "file:///var/www/app/.env"
    }
  }
}
```
Saves to: `/var/www/app/.env`

---

### Remote Deployment (SSH/Rsync)

Deploy artifacts to remote servers using `ssh://` or `rsync://` URIs. The tool uses your existing SSH and rsync configuration (`.ssh/config`, keys, etc.).

**SSH deployment:**
```json
{
  "artifacts": {
    "production.env": {
      "fragments": ["backend.prod"],
      "deployment": "ssh://prod-server/var/www/app/.env"
    }
  }
}
```
Uses `scp` to copy the file to the remote server.

**Rsync deployment:**
```json
{
  "artifacts": {
    "staging.env": {
      "fragments": ["backend.staging"],
      "deployment": "rsync://staging/home/deploy/.env"
    }
  }
}
```
Uses `rsync -az` to sync the file to the remote server.

**URI format:**

| URI | Host | Remote Path |
|-----|------|-------------|
| `ssh://myserver/var/www/.env` | myserver | /var/www/.env |
| `ssh://user@host/path/.env` | user@host | /path/.env |
| `rsync://backup/data/.env` | backup | /data/.env |

**Error handling:**

By default, remote deployment failures cause the tool to exit immediately. Set `exit_on_fail` to `false` to continue with other artifacts:

```json
{
  "artifacts": {
    "staging.env": {
      "fragments": ["backend.staging"],
      "deployment": "rsync://staging/home/deploy/.env",
      "exit_on_fail": false
    }
  }
}
```

**Note:** The artifact is always saved locally to `artifacts/<name>` first, then deployed to the remote location.

---

### Import Existing Files

Convert existing `.env` files into fragments.

```bash
managenv --import /path/to/existing.env --prefix backend
```

This:
1. Reads the existing file
2. Removes variables already defined in parent fragments (if any)
3. Saves unique variables to `fragments/backend.env`
4. Adds the fragment to your config

**With inheritance:**
```bash
# First import the base
managenv --import base.env --prefix database

# Then import prod (removes inherited vars automatically)
managenv --import prod.env --prefix database.prod
```

---

## Directory Structure

```
project/
├── managenv-config.json   # Configuration
├── managenv.py            # The tool
├── fragments/             # Your .env fragments
│   ├── backend.env
│   ├── backend.prod.env
│   └── frontend.env
├── artifacts/             # Generated .env files (default)
│   ├── dev.env
│   └── production.env
└── history/               # Automatic backups before overwrite
```

---

## Complete Example

**Config (`managenv-config.json`):**
```json
{
  "fragments": {
    "backend": {"uri": "file://fragments/backend.env"},
    "backend.prod": {"uri": "file://fragments/backend.prod.env"},
    "frontend": {"uri": "file://fragments/frontend.env"},
    "frontend.prod": {"uri": "file://fragments/frontend.prod.env"}
  },
  "artifacts": {
    "dev.env": ["backend", "frontend"],
    "production.env": {
      "fragments": ["backend.prod", "frontend.prod"],
      "deployment": "file:///var/www/app/.env"
    }
  }
}
```

**Generate:**
```bash
managenv
# Generated: artifacts/dev.env
# Generated: /var/www/app/.env
```
