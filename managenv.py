#!/usr/bin/env python3
"""
managenv - Combine .env fragments into artifact files with auto-inheritance.

Config format (v2):
  - "fragments": dict of {alias: {"uri": "..."}}
  - "artifacts": dict of artifact definitions
    - Brief: {name: [aliases]} (defaults deployment to artifacts/name)
    - Full: {name: {"fragments": [...], "deployment": "file://..."}}
  - Dot notation enables auto-inheritance (host.dev -> host, host.dev)

URI types supported (both file:// and plain paths work):
  - Relative: "fragments/backend.env" or "file://fragments/backend.env"
  - Absolute: "/etc/env/shared.env" or "file:///etc/env/shared.env"
  - URL: "https://config.example.com/base.env" (read-only)

Example config:
  {
    "fragments": {
      "backend": {"uri": "file://fragments/backend.env"},
      "backend.ovh": {"uri": "file://fragments/backend.ovh.env"},
      "shared": {"uri": "/etc/shared-env/common.env"}
    },
    "artifacts": {
      "ovh.env": {
        "fragments": ["backend.ovh", "shared"],
        "deployment": "file:///tmp/ovh.env"
      },
      "localhost.env": ["backend", "backend.localhost"]
    }
  }
"""

import argparse
import difflib
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


class Config:
    """Manages managenv configuration file operations."""

    def __init__(self, config_path: Path):
        """Initialize config manager.

        Args:
            config_path: Path to config file
        """
        self.config_path = config_path
        self._data: dict[str, Any] = {}
        if self.config_path.exists():
            self.load()

    def load(self) -> None:
        """Load config from file."""
        with open(self.config_path) as f:
            self._data = json.load(f)

    def save(self) -> None:
        """Save config to file."""
        with open(self.config_path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    def init(self) -> None:
        """Initialize a new config file."""
        if self.config_path.exists():
            raise FileExistsError(f"Config file already exists: {self.config_path}")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {"fragments": {}, "artifacts": {}}
        self.save()

    def get_fragments(self) -> dict[str, str]:
        """Get fragments mapping (alias -> uri).

        Returns:
            Dict of {alias: uri}
        """
        fragments_config = self._data.get("fragments", {})
        fragments = {}
        for alias, frag_def in fragments_config.items():
            if isinstance(frag_def, dict) and "uri" in frag_def:
                fragments[alias] = frag_def["uri"]
        return fragments

    def get_artifacts(self) -> dict[str, dict[str, Any]]:
        """Get artifacts mapping (name -> definition).

        Returns:
            Dict of {name: {"fragments": [...], "deployment": uri, "exit_on_fail": bool}}
        """
        artifacts_config = self._data.get("artifacts", {})
        artifacts = {}
        for name, art_def in artifacts_config.items():
            if isinstance(art_def, list):
                # Brief format: ["alias1", "alias2"]
                artifacts[name] = {
                    "fragments": art_def,
                    "deployment": f"file://artifacts/{name}",
                    "exit_on_fail": True,
                }
            elif isinstance(art_def, dict):
                # Full format
                artifacts[name] = {
                    "fragments": art_def.get("fragments", []),
                    "deployment": art_def.get("deployment", f"file://artifacts/{name}"),
                    "exit_on_fail": art_def.get("exit_on_fail", True),
                }
        return artifacts

    def add_fragment(self, alias: str, uri: str) -> None:
        """Add a new fragment to config.

        Args:
            alias: Fragment alias
            uri: Fragment URI

        Raises:
            ValueError: If fragment already exists
        """
        if "fragments" not in self._data:
            self._data["fragments"] = {}

        if alias in self._data["fragments"]:
            raise ValueError(f"Fragment '{alias}' already exists")

        self._data["fragments"][alias] = {"uri": uri}
        self.save()

    def remove_fragment(self, alias: str) -> None:
        """Remove a fragment from config.

        Args:
            alias: Fragment alias

        Raises:
            KeyError: If fragment doesn't exist
        """
        if "fragments" not in self._data or alias not in self._data["fragments"]:
            raise KeyError(f"Fragment '{alias}' not found")

        del self._data["fragments"][alias]
        self.save()

    def update_fragment(self, alias: str, uri: str) -> None:
        """Update an existing fragment's URI.

        Args:
            alias: Fragment alias
            uri: New fragment URI

        Raises:
            KeyError: If fragment doesn't exist
        """
        if "fragments" not in self._data or alias not in self._data["fragments"]:
            raise KeyError(f"Fragment '{alias}' not found")

        self._data["fragments"][alias]["uri"] = uri
        self.save()

    def add_artifact(self, name: str, fragments: list[str], deployment: str) -> None:
        """Add a new artifact to config.

        Args:
            name: Artifact name
            fragments: List of fragment aliases
            deployment: Deployment URI

        Raises:
            ValueError: If artifact already exists
        """
        if "artifacts" not in self._data:
            self._data["artifacts"] = {}

        if name in self._data["artifacts"]:
            raise ValueError(f"Artifact '{name}' already exists")

        self._data["artifacts"][name] = {
            "fragments": fragments,
            "deployment": deployment,
        }
        self.save()

    def remove_artifact(self, name: str) -> None:
        """Remove an artifact from config.

        Args:
            name: Artifact name

        Raises:
            KeyError: If artifact doesn't exist
        """
        if "artifacts" not in self._data or name not in self._data["artifacts"]:
            raise KeyError(f"Artifact '{name}' not found")

        del self._data["artifacts"][name]
        self.save()

    def update_artifact(
        self,
        name: str,
        fragments: list[str] | None = None,
        deployment: str | None = None,
    ) -> None:
        """Update an existing artifact.

        Args:
            name: Artifact name
            fragments: New list of fragment aliases (optional)
            deployment: New deployment URI (optional)

        Raises:
            KeyError: If artifact doesn't exist
        """
        if "artifacts" not in self._data or name not in self._data["artifacts"]:
            raise KeyError(f"Artifact '{name}' not found")

        artifact = self._data["artifacts"][name]
        if not isinstance(artifact, dict):
            # Convert brief format to full format
            artifact = {
                "fragments": artifact,
                "deployment": f"file://artifacts/{name}",
            }
            self._data["artifacts"][name] = artifact

        if fragments is not None:
            artifact["fragments"] = fragments
        if deployment is not None:
            artifact["deployment"] = deployment

        self.save()


def is_url(uri: str) -> bool:
    """Check if URI is a URL."""
    return uri.startswith("http://") or uri.startswith("https://")


def normalize_uri(uri: str) -> str:
    """Normalize URI by stripping file:// prefix if present.

    Handles:
      - file:///absolute/path -> /absolute/path (absolute)
      - file://relative/path -> relative/path (relative)
      - plain/path -> plain/path (unchanged)
    """
    if uri.startswith("file:///"):
        # Absolute path: file:///path -> /path
        return uri[7:]  # Keep one slash for absolute
    elif uri.startswith("file://"):
        # Relative path: file://path -> path
        return uri[7:]
    return uri


def is_remote_deployment(uri: str) -> bool:
    """Check if deployment URI is remote (ssh/rsync)."""
    return uri.startswith("ssh://") or uri.startswith("rsync://")


def parse_remote_uri(uri: str) -> tuple[str, str, str]:
    """Parse remote URI into (protocol, host, path).

    Examples:
      ssh://myserver/var/www/.env -> ('ssh', 'myserver', '/var/www/.env')
      rsync://user@host/path -> ('rsync', 'user@host', '/path')
    """
    if uri.startswith("ssh://"):
        rest = uri[6:]  # Remove 'ssh://'
        protocol = "ssh"
    elif uri.startswith("rsync://"):
        rest = uri[8:]  # Remove 'rsync://'
        protocol = "rsync"
    else:
        raise ValueError(f"Unknown remote protocol: {uri}")

    # Split host and path at first /
    slash_idx = rest.find("/")
    if slash_idx == -1:
        raise ValueError(f"Invalid remote URI (no path): {uri}")

    host = rest[:slash_idx]
    path = rest[slash_idx:]  # Keep leading /

    return protocol, host, path


def deploy_remote(local_path: Path, deployment_uri: str) -> tuple[bool, str]:
    """Deploy a local file to a remote location via ssh or rsync.

    Args:
        local_path: Path to the local file to deploy
        deployment_uri: Remote URI (ssh:// or rsync://)

    Returns:
        (success, message) tuple
    """
    protocol, host, remote_path = parse_remote_uri(deployment_uri)

    if protocol == "ssh":
        # Use scp for ssh:// URIs
        cmd = ["scp", str(local_path), f"{host}:{remote_path}"]
    elif protocol == "rsync":
        # Use rsync for rsync:// URIs
        cmd = ["rsync", "-az", str(local_path), f"{host}:{remote_path}"]
    else:
        return False, f"Unknown protocol: {protocol}"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        if result.returncode == 0:
            return True, f"Deployed to {host}:{remote_path}"
        else:
            error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return False, f"Failed to deploy: {error}"
    except subprocess.TimeoutExpired:
        return False, "Deployment timed out after 5 minutes"
    except FileNotFoundError:
        cmd_name = "scp" if protocol == "ssh" else "rsync"
        return False, f"Command not found: {cmd_name}"


def fetch_source_content(uri: str, base_dir: Path, cache: dict[str, str]) -> str:
    """Fetch content from a URI (URL, absolute path, or relative path).

    Args:
        uri: The URI to fetch from (supports file:// prefix)
        base_dir: Base directory for resolving relative paths
        cache: Cache dict for URL content (reused per run)

    Returns:
        Content as string

    Raises:
        SystemExit: If fetch fails (URL error or file not found)
    """
    # Check cache first (for URLs)
    if uri in cache:
        return cache[uri]

    try:
        if is_url(uri):
            # Fetch from URL
            with urllib.request.urlopen(uri, timeout=30) as response:
                content = response.read().decode("utf-8")
            cache[uri] = content
            return content

        # Normalize file:// URIs to plain paths
        path = normalize_uri(uri)

        if path.startswith("/"):
            # Absolute path
            filepath = Path(path)
        else:
            # Relative path (resolved against base_dir)
            filepath = base_dir / path

        if not filepath.exists():
            print(f"Error: File not found: {filepath}")
            raise SystemExit(1)
        return filepath.read_text()
    except urllib.error.URLError as e:
        print(f"Error: Failed to fetch URL {uri}: {e}")
        raise SystemExit(1)


def load_config(
    config_path: Path,
) -> tuple[dict[str, str], dict[str, dict[str, str | list[str] | bool]]]:
    """Load config, return (fragments, artifacts).

    Config format (v2):
      - "fragments": dict of {alias: {"uri": "..."}}
        URI can be: URL, absolute path, relative path, or file:// prefixed
      - "artifacts": dict of artifact definitions
        Brief: {name: [aliases]} -> defaults deployment to artifacts/name
        Full: {name: {"fragments": [...], "deployment": "...", "exit_on_fail": bool}}

    Returns:
        fragments: {alias: uri} mapping
        artifacts: {name: {"fragments": [...], "deployment": uri, "exit_on_fail": bool}} mapping
    """
    config = Config(config_path)
    return config.get_fragments(), config.get_artifacts()


def list_config(config_path: Path) -> None:
    """List all fragments and artifacts from config."""
    config = Config(config_path)
    fragments = config.get_fragments()
    artifacts = config.get_artifacts()

    print("Fragments:")
    if fragments:
        for alias, uri in fragments.items():
            print(f"  {alias} -> {uri}")
    else:
        print("  (none)")

    print("\nArtifacts:")
    if artifacts:
        for name, art_def in artifacts.items():
            frags = art_def["fragments"]
            deployment = art_def["deployment"]
            print(f"  {name}")
            print(f"    fragments: {frags}")
            print(f"    deployment: {deployment}")
    else:
        print("  (none)")


def check_uri_accessible(uri: str, base_dir: Path) -> str | None:
    """Check if a URI is accessible. Returns error message or None if OK."""
    try:
        if is_url(uri):
            # Just check if URL is reachable
            with urllib.request.urlopen(uri, timeout=10) as response:
                pass
            return None

        # Normalize file:// URIs to plain paths
        path = normalize_uri(uri)

        if path.startswith("/"):
            filepath = Path(path)
        else:
            filepath = base_dir / path

        if not filepath.exists():
            return f"File not found: {filepath}"
        return None
    except urllib.error.URLError as e:
        return f"URL error: {e}"


def validate_config(config_path: Path, base_dir: Path) -> int:
    """Validate config for errors. Returns 0 if valid, 1 if errors."""
    errors = []
    warnings = []

    # Check if config file exists
    if not config_path.exists():
        print("Errors:")
        print(f"  Config file not found: {config_path}")
        return 1

    # Try to load and parse JSON
    try:
        fragments, artifacts = load_config(config_path)
    except json.JSONDecodeError as e:
        print("Errors:")
        print(f"  Invalid JSON in config file: {e}")
        return 1
    except Exception as e:
        print("Errors:")
        print(f"  Failed to load config: {e}")
        return 1

    # Check all fragment URIs are accessible
    for alias, uri in fragments.items():
        error = check_uri_accessible(uri, base_dir)
        if error:
            errors.append(f"{error} (fragment '{alias}')")

    # Check all aliases in artifacts are defined
    for art_name, art_def in artifacts.items():
        for alias in art_def["fragments"]:
            # Check if alias or any parent exists
            parts = alias.split(".")
            found = False
            for i in range(len(parts), 0, -1):
                check = ".".join(parts[:i])
                if check in fragments:
                    found = True
                    break
            if not found:
                errors.append(f"Undefined fragment '{alias}' in artifact '{art_name}'")

    # Print results
    if errors:
        print("Errors:")
        for err in errors:
            print(f"  {err}")
    if warnings:
        print("Warnings:")
        for warn in warnings:
            print(f"  {warn}")
    if not errors and not warnings:
        print("Config is valid.")

    return 1 if errors else 0


def resolve_deployment_path(deployment_uri: str, base_dir: Path) -> Path:
    """Resolve deployment URI to a file path.

    Handles:
      - file:///absolute/path -> /absolute/path
      - file://relative/path -> base_dir/relative/path
      - plain/path -> base_dir/plain/path
    """
    path = normalize_uri(deployment_uri)
    if path.startswith("/"):
        return Path(path)
    return base_dir / path


def diff_artifacts(
    config_path: Path,
    base_dir: Path,
    specific_artifacts: list[str] | None = None,
) -> None:
    """Show diff of what would change in artifact files."""
    fragments, artifacts = load_config(config_path)
    cache: dict[str, str] = {}

    if specific_artifacts:
        unknown = [a for a in specific_artifacts if a not in artifacts]
        if unknown:
            print(f"Error: unknown artifact(s): {', '.join(unknown)}")
            return
        artifacts = {k: artifacts[k] for k in specific_artifacts}

    for art_name, art_def in artifacts.items():
        aliases = art_def["fragments"]
        deployment_uri = art_def["deployment"]

        # Generate new content
        expanded = []
        seen = set()
        for alias in aliases:
            for resolved in resolve_inheritance(alias, fragments):
                if resolved not in seen:
                    expanded.append(resolved)
                    seen.add(resolved)

        merged = {}
        fragment_uris = []
        for alias in expanded:
            uri = fragments[alias]
            content = fetch_source_content(uri, base_dir, cache)
            fragment_uris.append(uri)
            merged.update(parse_env(content))

        header = f"# Generated by managenv\n# Fragments: {' -> '.join(fragment_uris)}\n\n"
        body = "\n".join(f"{k}={v}" for k, v in merged.items())
        new_content = header + body + "\n"

        output_path = resolve_deployment_path(deployment_uri, base_dir)
        if output_path.exists():
            old_content = output_path.read_text()
            if old_content == new_content:
                print(f"=== {art_name} === (no changes)")
            else:
                print(f"=== {art_name} ===")
                diff = difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    new_content.splitlines(keepends=True),
                    fromfile=f"a/{art_name}",
                    tofile=f"b/{art_name}",
                )
                print("".join(diff))
        else:
            print(f"=== {art_name} === (new file)")
            print(new_content)


def resolve_inheritance(alias: str, snippets: dict[str, str]) -> list[str]:
    """Expand alias to include parent aliases via dot notation."""
    parts = alias.split(".")
    chain = []
    for i in range(1, len(parts) + 1):
        parent = ".".join(parts[:i])
        if parent in snippets:
            chain.append(parent)
    return chain


def parse_env(content: str) -> dict[str, str]:
    """Parse .env content into key-value dict."""
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def backup_if_exists(output_path: Path, history_dir: Path) -> None:
    """Backup existing output file to history directory."""
    if output_path.exists():
        history_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_name = f"{timestamp}_{output_path.name}"
        shutil.copy(output_path, history_dir / backup_name)


def generate_artifact(
    art_name: str,
    art_def: dict[str, str | list[str] | bool],
    fragments: dict[str, str],
    base_dir: Path,
    history_dir: Path,
    cache: dict[str, str],
    dry_run: bool = False,
) -> str:
    """Generate a single artifact file from fragment aliases."""
    aliases = art_def["fragments"]
    deployment_uri = art_def["deployment"]
    exit_on_fail = art_def.get("exit_on_fail", True)

    # Expand all aliases with inheritance
    expanded = []
    seen = set()
    for alias in aliases:
        for resolved in resolve_inheritance(alias, fragments):
            if resolved not in seen:
                expanded.append(resolved)
                seen.add(resolved)

    # Merge env files in order
    merged = {}
    fragment_uris = []
    for alias in expanded:
        uri = fragments[alias]
        content = fetch_source_content(uri, base_dir, cache)
        fragment_uris.append(uri)
        merged.update(parse_env(content))

    # Build output content
    header = f"# Generated by managenv\n# Fragments: {' -> '.join(fragment_uris)}\n\n"
    body = "\n".join(f"{k}={v}" for k, v in merged.items())
    output_content = header + body + "\n"

    if dry_run:
        print(f"=== {art_name} ===")
        print(output_content)
    else:
        # Always save to local artifacts/<name> first
        local_path = base_dir / "artifacts" / art_name
        backup_if_exists(local_path, history_dir)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(output_content)
        print(f"Generated: {local_path}")

        # Handle deployment
        if is_remote_deployment(deployment_uri):
            # Remote deployment via ssh/rsync
            success, message = deploy_remote(local_path, deployment_uri)
            if success:
                print(f"  -> {message}")
            else:
                print(f"  -> ERROR: {message}")
                if exit_on_fail:
                    sys.exit(1)
        else:
            # Local deployment - write to that path if different from artifacts folder
            output_path = resolve_deployment_path(deployment_uri, base_dir)
            if output_path != local_path:
                backup_if_exists(output_path, history_dir)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(output_content)
                print(f"  -> Deployed: {output_path}")

    return output_content


def get_parent_vars(
    prefix: str, fragments: dict[str, str], base_dir: Path, cache: dict[str, str]
) -> set[str]:
    """Get all variable names from parent prefixes."""
    parent_vars = set()
    parts = prefix.split(".")
    for i in range(1, len(parts) + 1):
        parent = ".".join(parts[:i])
        if parent in fragments:
            uri = fragments[parent]
            try:
                content = fetch_source_content(uri, base_dir, cache)
                parent_vars.update(parse_env(content).keys())
            except SystemExit:
                # Fragment not accessible, skip
                pass
    return parent_vars


def import_env(
    import_path: Path,
    prefix: str,
    config_path: Path,
    base_dir: Path,
) -> None:
    """Import an env file as a new fragment, removing inherited vars.

    Imported files are stored in the 'fragments/' folder by default.
    """
    fragments_dir = base_dir / "fragments"

    # Auto-create config if it doesn't exist
    if not config_path.exists():
        config = Config(config_path)
        config.init()
        print(f"Created: {config_path}")
    else:
        config = Config(config_path)

    # Get fragments from config
    fragments = config.get_fragments()

    # Determine alias name
    if prefix in fragments:
        # Prefix exists, use prefix.basename
        basename = import_path.stem  # filename without extension
        alias = f"{prefix}.{basename}"
    else:
        alias = prefix

    # Check for alias conflict
    if alias in fragments:
        print(f"Error: Fragment alias '{alias}' already exists")
        print(f"Suggestions:")
        print(f"  1. Use a different --fragment name")
        print(f"  2. Remove existing fragment from config first")
        print(f"  3. Use --fragment '{alias}.{import_path.stem}' to create a child fragment")
        raise SystemExit(1)

    # Get vars from parent prefixes to exclude
    cache: dict[str, str] = {}
    parent_vars = get_parent_vars(prefix, fragments, base_dir, cache)

    # Parse import file and remove inherited vars
    import_content = import_path.read_text()
    import_vars = parse_env(import_content)
    unique_vars = {k: v for k, v in import_vars.items() if k not in parent_vars}

    # Create new fragment file in fragments/ folder
    new_filename = f"{alias.replace('.', '_')}.env"
    new_filepath = fragments_dir / new_filename
    fragments_dir.mkdir(parents=True, exist_ok=True)

    # Check for file conflict
    if new_filepath.exists():
        print(f"Error: File '{new_filepath}' already exists")
        print(f"Suggestions:")
        print(f"  1. Use a different --fragment name")
        print(f"  2. Remove or rename existing file first")
        print(f"  3. Use --fragment '{alias}.v2' to create a versioned fragment")
        raise SystemExit(1)

    # Write fragment with only unique vars
    lines = [f"# Imported from {import_path.name}", f"# Alias: {alias}", ""]
    lines.extend(f"{k}={v}" for k, v in unique_vars.items())
    new_filepath.write_text("\n".join(lines) + "\n")

    # Add fragment to config using Config class
    try:
        config.add_fragment(alias, f"file://fragments/{new_filename}")
    except ValueError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

    removed_count = len(import_vars) - len(unique_vars)
    print(f"Imported: {import_path} -> {alias}")
    print(f"  Created: {new_filepath}")
    print(f"  Variables: {len(unique_vars)} kept, {removed_count} removed (inherited)")


def add_artifact(
    artifact_name: str,
    fragments_list: str,
    config_path: Path,
    uri: str | None = None,
) -> str:
    """Add a new artifact definition to config.

    Args:
        artifact_name: Label (e.g. 'localhost')
        fragments_list: Comma-separated list of fragment aliases
        config_path: Path to config file
        uri: Optional custom deployment URI

    Returns:
        The artifact key that was added
    """
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        print("Run with --init to create a config file first")
        raise SystemExit(1)

    # Parse fragments list
    fragments = [f.strip() for f in fragments_list.split(",") if f.strip()]
    if not fragments:
        print("Error: At least one fragment is required")
        raise SystemExit(1)

    # Validate that all fragment aliases exist in config
    config = Config(config_path)
    known_fragments = config.get_fragments()
    missing = [f for f in fragments if f not in known_fragments]
    if missing:
        print(f"Error: Unknown fragment(s): {', '.join(missing)}")
        print(f"Available fragments: {', '.join(known_fragments) or '(none)'}")
        raise SystemExit(1)

    # Determine deployment
    if uri:
        deployment = uri
    else:
        deployment = f"file://artifacts/{artifact_name}.env"

    # Use Config class to add artifact
    try:
        config.add_artifact(artifact_name, fragments, deployment)
        print(f"Added artifact: {artifact_name}")
        print(f"  Fragments: {fragments}")
        print(f"  Deployment: {deployment}")
        return artifact_name
    except ValueError as e:
        print(f"Error: {e}")
        print(f"Suggestions:")
        print(f"  1. Use a different artifact name")
        print(f"  2. Remove existing artifact from config first")
        raise SystemExit(1)


def generate_completion_script(shell: str) -> str:
    """Generate a shell completion script for bash or zsh."""
    if shell == "bash":
        return _bash_completion_script()
    elif shell == "zsh":
        return _zsh_completion_script()
    return ""


def _bash_completion_script() -> str:
    return r'''_managenv_complete() {
    local cur prev words cword
    _init_completion || return

    local flags="-c -a --config --artifact --dry-run --import --add --delete --uri --deploy --list --validate --diff --init --scripts --apply"

    # Find config path from command line
    local config_path=""
    local i
    for (( i=1; i < ${#words[@]}-1; i++ )); do
        if [[ "${words[i]}" == "-c" || "${words[i]}" == "--config" ]]; then
            config_path="${words[i+1]}"
            break
        fi
    done
    if [[ -z "$config_path" ]]; then
        local script_dir
        script_dir="$(dirname "$(which managenv 2>/dev/null || echo "managenv.py")")"
        config_path="${script_dir}/managenv.json"
    fi

    # Helper: get artifact names from config
    _managenv_artifacts() {
        python3 -c "
import json, sys
try:
    d = json.load(open('$config_path'))
    print(' '.join(d.get('artifacts', {}).keys()))
except: pass
" 2>/dev/null
    }

    # Helper: get fragment names from config
    _managenv_fragments() {
        python3 -c "
import json, sys
try:
    d = json.load(open('$config_path'))
    print(' '.join(d.get('fragments', {}).keys()))
except: pass
" 2>/dev/null
    }

    # Determine which flag we are completing arguments for
    # Walk backwards to find the active flag and count positional args after it
    local active_flag=""
    local arg_pos=0
    for (( i=1; i < cword; i++ )); do
        case "${words[i]}" in
            -c|--config|-a|--artifact|--uri|--delete|--scripts|--diff)
                active_flag="${words[i]}"
                arg_pos=0
                ;;
            --import|--add)
                active_flag="${words[i]}"
                arg_pos=0
                ;;
            --dry-run|--deploy|--list|--validate|--init|--apply)
                active_flag=""
                arg_pos=0
                ;;
            -*)
                active_flag=""
                arg_pos=0
                ;;
            *)
                (( arg_pos++ ))
                ;;
        esac
    done

    case "$active_flag" in
        --scripts)
            if [[ $arg_pos -eq 0 ]]; then
                COMPREPLY=( $(compgen -W "bash zsh" -- "$cur") )
                return
            fi
            ;;
        -a|--artifact|--diff)
            if [[ $arg_pos -eq 0 ]]; then
                COMPREPLY=( $(compgen -W "$(_managenv_artifacts)" -- "$cur") )
                return
            fi
            ;;
        --delete)
            if [[ $arg_pos -eq 0 ]]; then
                COMPREPLY=( $(compgen -W "$(_managenv_artifacts)" -- "$cur") )
                return
            fi
            ;;
        -c|--config|--uri)
            if [[ $arg_pos -eq 0 ]]; then
                _filedir
                return
            fi
            ;;
        --import)
            if [[ $arg_pos -eq 0 ]]; then
                _filedir
                return
            elif [[ $arg_pos -eq 1 ]]; then
                COMPREPLY=( $(compgen -W "$(_managenv_fragments)" -- "$cur") )
                return
            fi
            ;;
        --add)
            if [[ $arg_pos -eq 0 ]]; then
                # First arg is a new name, no completion
                return
            elif [[ $arg_pos -eq 1 ]]; then
                # Comma-separated fragments
                local prefix=""
                if [[ "$cur" == *,* ]]; then
                    prefix="${cur%,*},"
                    cur="${cur##*,}"
                fi
                local fragments
                fragments="$(_managenv_fragments)"
                local completions
                completions=( $(compgen -W "$fragments" -- "$cur") )
                COMPREPLY=( "${completions[@]/#/$prefix}" )
                return
            fi
            ;;
    esac

    # Default: complete flags or artifact names (positional args)
    if [[ "$cur" == -* ]]; then
        COMPREPLY=( $(compgen -W "$flags" -- "$cur") )
        return
    fi

    # Positional: complete artifact names with comma-separation support
    local prefix=""
    if [[ "$cur" == *,* ]]; then
        prefix="${cur%,*},"
        cur="${cur##*,}"
    fi
    local artifacts
    artifacts="$(_managenv_artifacts)"
    if [[ -n "$artifacts" ]]; then
        local completions
        completions=( $(compgen -W "$artifacts" -- "$cur") )
        COMPREPLY=( "${completions[@]/#/$prefix}" )
        return
    fi

    COMPREPLY=( $(compgen -W "$flags" -- "$cur") )
    return
}

complete -F _managenv_complete managenv
complete -F _managenv_complete managenv.py'''


def _zsh_completion_script() -> str:
    return r'''#compdef managenv managenv.py

_managenv() {
    local -a flags
    flags=(
        '-c[Config file path]:config file:_files'
        '--config[Config file path]:config file:_files'
        '*-a[Generate specific artifact (repeatable)]:artifact:_managenv_artifacts'
        '*--artifact[Generate specific artifact (repeatable)]:artifact:_managenv_artifacts'
        '--dry-run[Print output without writing]'
        '--import[Import env file with fragment alias]:file:_files:fragment:_managenv_fragments'
        '--add[Add new artifact definition]:name: :fragments:_managenv_fragments_csv'
        '--delete[Remove an artifact from config]:artifact:_managenv_artifacts'
        '--uri[Custom deployment URI]:uri:_files'
        '--deploy[Deploy artifact after adding]'
        '--list[List all fragments and artifacts]'
        '--validate[Validate config for errors]'
        '*--diff[Show diff for specific artifact (repeatable)]:artifact:_managenv_artifacts'
        '--init[Create a basic config file]'
        '--scripts[Output shell completion script]:shell:(bash zsh)'
        '--apply[Install completion to rc file]'
    )

    _arguments -s -S $flags '*:artifact:_managenv_artifacts_csv'
}

_managenv_config_path() {
    local config_path=""
    local i
    for (( i=1; i < ${#words[@]}; i++ )); do
        if [[ "${words[i]}" == "-c" || "${words[i]}" == "--config" ]]; then
            config_path="${words[i+1]}"
            break
        fi
    done
    if [[ -z "$config_path" ]]; then
        local script_dir
        script_dir="$(dirname "$(which managenv 2>/dev/null || echo "managenv.py")")"
        config_path="${script_dir}/managenv.json"
    fi
    echo "$config_path"
}

_managenv_artifacts() {
    local config_path
    config_path="$(_managenv_config_path)"
    local -a artifacts
    artifacts=( ${(f)"$(python3 -c "
import json
try:
    d = json.load(open('$config_path'))
    print('\n'.join(d.get('artifacts', {}).keys()))
except: pass
" 2>/dev/null)"} )
    compadd -a artifacts
}

_managenv_fragments() {
    local config_path
    config_path="$(_managenv_config_path)"
    local -a fragments
    fragments=( ${(f)"$(python3 -c "
import json
try:
    d = json.load(open('$config_path'))
    print('\n'.join(d.get('fragments', {}).keys()))
except: pass
" 2>/dev/null)"} )
    compadd -a fragments
}

_managenv_fragments_csv() {
    local config_path
    config_path="$(_managenv_config_path)"
    local -a fragments
    fragments=( ${(f)"$(python3 -c "
import json
try:
    d = json.load(open('$config_path'))
    print('\n'.join(d.get('fragments', {}).keys()))
except: pass
" 2>/dev/null)"} )

    # Handle comma-separated completion
    local prefix=""
    if [[ "$words[CURRENT]" == *,* ]]; then
        prefix="${words[CURRENT]%,*},"
    fi
    compadd -p "$prefix" -a fragments
}

_managenv_artifacts_csv() {
    local config_path
    config_path="$(_managenv_config_path)"
    local -a artifacts
    artifacts=( ${(f)"$(python3 -c "
import json
try:
    d = json.load(open('$config_path'))
    print('\n'.join(d.get('artifacts', {}).keys()))
except: pass
" 2>/dev/null)"} )

    # Handle comma-separated completion
    local prefix=""
    if [[ "$words[CURRENT]" == *,* ]]; then
        prefix="${words[CURRENT]%,*},"
    fi
    compadd -p "$prefix" -a artifacts
}

_managenv "$@"'''


def main():
    parser = argparse.ArgumentParser(description="Merge .env fragments into artifact files")
    script_dir = Path(__file__).parent.resolve()
    default_config = script_dir / "managenv.json"
    parser.add_argument("-c", "--config", default=str(default_config), help="Config file path")
    parser.add_argument("-a", "--artifact", action="append", dest="artifact",
                        help="Generate specific artifact (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="Print output without writing")
    parser.add_argument("--import", dest="import_file", nargs=2, metavar=("FILE", "FRAGMENT"), help="Import env file with fragment alias")
    parser.add_argument("--add", nargs=2, metavar=("NAME", "FRAGMENTS"), help="Add new artifact definition (NAME and comma-separated FRAGMENTS)")
    parser.add_argument("--delete", metavar="ARTIFACT", help="Remove an artifact from config")
    parser.add_argument("--uri", help="Custom deployment URI (optional with --add)")
    parser.add_argument("--deploy", action="store_true", help="Deploy artifact after adding (use with --add)")
    parser.add_argument("--list", action="store_true", help="List all fragments and artifacts")
    parser.add_argument("--validate", action="store_true", help="Validate config for errors")
    parser.add_argument("--diff", action="append", dest="diff", metavar="ARTIFACT",
                        help="Show diff for specific artifact (repeatable)")
    parser.add_argument("artifacts_positional", nargs="*", default=[],
                        metavar="ARTIFACT",
                        help="Artifacts to generate (comma-separated or space-separated)")
    parser.add_argument("--init", action="store_true", help="Create a basic config file")
    parser.add_argument("--scripts", choices=["bash", "zsh"], metavar="SHELL",
                        help="Output shell completion script (bash or zsh)")
    parser.add_argument("--apply", action="store_true",
                        help="Install completion script to ~/.bashrc or ~/.zshrc (use with --scripts)")
    args = parser.parse_args()

    # Resolve target artifacts from -a flags and positional args
    target_artifacts: list[str] = []
    if args.artifact:
        target_artifacts.extend(args.artifact)
    for pos in args.artifacts_positional:
        target_artifacts.extend(a.strip() for a in pos.split(",") if a.strip())
    # Deduplicate preserving order
    seen: set[str] = set()
    target_artifacts = [a for a in target_artifacts if not (a in seen or seen.add(a))]

    config_path = Path(args.config)
    base_dir = config_path.parent if config_path.parent != Path() else Path(".")
    history_dir = base_dir / "history"

    # Handle --scripts (shell completion)
    if args.scripts:
        script = generate_completion_script(args.scripts)
        if args.apply:
            rc_file = Path.home() / (".bashrc" if args.scripts == "bash" else ".zshrc")
            marker = "# managenv completions"
            rc_content = rc_file.read_text() if rc_file.exists() else ""
            if marker in rc_content:
                print(f"Completions already installed in {rc_file}")
            else:
                with open(rc_file, "a") as f:
                    f.write(f"\n{marker}\n{script}\n")
                print(f"Completions installed to {rc_file}")
        else:
            print(script)
        return 0

    # Handle --init
    if args.init:
        try:
            config = Config(config_path)
            config.init()
            print(f"Created: {config_path}")
            return 0
        except FileExistsError:
            print(f"Error: Config file already exists: {config_path}")
            return 1

    # Handle import mode (can auto-create config)
    if args.import_file:
        import_env(Path(args.import_file[0]), args.import_file[1], config_path, base_dir)
        return 0

    # Handle --add
    if args.add:
        # Add artifact to config
        artifact_key = add_artifact(args.add[0], args.add[1], config_path, args.uri)

        # Deploy if --deploy flag is set
        if args.deploy:
            fragments, artifacts = load_config(config_path)
            cache: dict[str, str] = {}

            # Generate the newly added artifact
            if artifact_key in artifacts:
                generate_artifact(
                    artifact_key, artifacts[artifact_key], fragments, base_dir, history_dir, cache, False
                )

        return 0

    # Handle --delete
    if args.delete:
        if not config_path.exists():
            print(f"Error: Config file not found: {config_path}")
            return 1

        config = Config(config_path)
        try:
            config.remove_artifact(args.delete)
            print(f"Removed artifact: {args.delete}")
            return 0
        except KeyError as e:
            print(f"Error: {e}")
            return 1

    # Commands that require existing config
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}")
        return 1

    # Handle --list
    if args.list:
        list_config(config_path)
        return 0

    # Handle --validate
    if args.validate:
        return validate_config(config_path, base_dir)

    # Handle --diff
    if args.diff is not None:
        diff_artifacts(config_path, base_dir, args.diff)
        return 0

    # Default: generate artifacts
    fragments, artifacts = load_config(config_path)
    cache: dict[str, str] = {}  # Cache for URL content

    if target_artifacts:
        unknown = [a for a in target_artifacts if a not in artifacts]
        if unknown:
            print(f"Error: unknown artifact(s): {', '.join(unknown)}")
            return 1
        artifacts = {k: artifacts[k] for k in target_artifacts}

    for art_name, art_def in artifacts.items():
        generate_artifact(
            art_name, art_def, fragments, base_dir, history_dir, cache, args.dry_run
        )

    return 0


if __name__ == "__main__":
    exit(main())
