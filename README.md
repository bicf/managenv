# managenv

A simple tool to manage multiple `.env` files by combining reusable snippets.

## The Problem

When working with multiple environments (local, dev, staging, production), you often end up with duplicate variables across `.env` files. When a value changes, you have to update it in multiple places.

## The Solution

managenv lets you define small `.env` snippets and combine them into output files. Shared variables live in one place.

## Quick Example

**1. Create snippets in `source/`:**

```env
# source/base.env
APP_NAME=myapp
LOG_LEVEL=info

# source/database.env
DB_HOST=localhost
DB_PORT=5432

# source/prod_database.env
DB_HOST=prod.example.com
```

**2. Configure in `managenv-config.json`:**

```json
{
  "base": "base.env",
  "database": "database.env",
  "database.prod": "prod_database.env",

  "local.env": ["base", "database"],
  "production.env": ["base", "database.prod"]
}
```

**3. Generate:**

```bash
python managenv.py
```

**Output:**
- `output/local.env` - base + database (DB_HOST=localhost)
- `output/production.env` - base + database.prod (DB_HOST=prod.example.com)

## Auto-Inheritance

Use dot notation for automatic inheritance. When you reference `database.prod`, it automatically includes `database` first, then `database.prod` (which can override values).

## Commands

```bash
python managenv.py                     # Generate all outputs
python managenv.py -o local.env        # Generate one output
python managenv.py --dry-run           # Preview without writing
python managenv.py --list              # List all snippets and outputs
python managenv.py --validate          # Check config for errors
python managenv.py --diff              # Show what would change
python managenv.py --import file.env --prefix name  # Import existing env file
```

The `--import` command auto-creates `managenv-config.json` if it doesn't exist.

## Directory Structure

```
├── managenv-config.json   # Configuration
├── source/                # Your .env snippets
├── output/                # Generated .env files
└── history/               # Automatic backups
```
