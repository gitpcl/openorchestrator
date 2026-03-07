"""Self-update functionality for Open Orchestrator.

Provides version checking and self-update capabilities by pulling from GitHub
and reinstalling the package.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from open_orchestrator.__version__ import __version__

logger = logging.getLogger(__name__)


class UpdateInfo:
    """Information about an available update."""

    def __init__(
        self,
        current_version: str,
        latest_version: str,
        update_available: bool,
        release_url: str | None = None,
        release_notes: str | None = None,
    ):
        self.current_version = current_version
        self.latest_version = latest_version
        self.update_available = update_available
        self.release_url = release_url
        self.release_notes = release_notes


class Updater:
    """Handles self-update operations for Open Orchestrator."""

    GITHUB_API = "https://api.github.com/repos/gitpcl/openorchestrator"
    GITHUB_REPO = "https://github.com/gitpcl/openorchestrator.git"

    def __init__(self, install_path: Path | None = None):
        """Initialize updater.

        Args:
            install_path: Path to the installation directory. If None, attempts to detect.
        """
        self.current_version = __version__
        self.install_path = install_path or self._detect_install_path()

    def _detect_install_path(self) -> Path | None:
        """Detect the installation path by finding the git repository root.

        Returns:
            Path to installation directory, or None if not found.
        """
        try:
            # Try to find the git repository root
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
                check=True,
            )
            return Path(result.stdout.strip())
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Could not detect installation path")
            return None

    def check_for_updates(self, include_prereleases: bool = True) -> UpdateInfo:
        """Check if a new version is available on GitHub.

        Args:
            include_prereleases: Whether to include pre-release versions (default: True)

        Returns:
            UpdateInfo with details about available updates.
        """
        try:
            # Fetch all releases from GitHub API (includes pre-releases)
            url = f"{self.GITHUB_API}/releases"
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                releases = json.loads(response.read().decode())

            if not releases:
                raise ValueError("No releases found")

            # Filter releases based on prerelease preference
            if not include_prereleases:
                releases = [r for r in releases if not r.get("prerelease", False)]

            if not releases:
                raise ValueError("No stable releases found")

            # Get the first release (most recent)
            latest_release = releases[0]
            latest_version = latest_release.get("tag_name", "").lstrip("v")
            release_url = latest_release.get("html_url")
            release_notes = latest_release.get("body", "")
            is_prerelease = latest_release.get("prerelease", False)

            # Compare versions
            update_available = self._is_newer_version(latest_version, self.current_version)

            # Add pre-release indicator to notes if applicable
            if is_prerelease and release_notes:
                release_notes = f"⚠️  **Pre-release version**\n\n{release_notes}"

            return UpdateInfo(
                current_version=self.current_version,
                latest_version=latest_version,
                update_available=update_available,
                release_url=release_url,
                release_notes=release_notes,
            )

        except Exception as e:
            logger.error(f"Failed to check for updates: {e}")
            return UpdateInfo(
                current_version=self.current_version,
                latest_version=self.current_version,
                update_available=False,
            )

    def _is_newer_version(self, latest: str, current: str) -> bool:
        """Compare version strings.

        Args:
            latest: Latest version string
            current: Current version string

        Returns:
            True if latest is newer than current
        """
        try:
            import re

            # Strip pre-release suffixes before parsing (e.g. 0.3.0-alpha -> 0.3.0)
            latest_clean = re.split(r"[-+]", latest, maxsplit=1)[0]
            current_clean = re.split(r"[-+]", current, maxsplit=1)[0]
            latest_parts = [int(x) for x in latest_clean.split(".")]
            current_parts = [int(x) for x in current_clean.split(".")]

            # Pad to same length
            max_len = max(len(latest_parts), len(current_parts))
            latest_parts.extend([0] * (max_len - len(latest_parts)))
            current_parts.extend([0] * (max_len - len(current_parts)))

            return latest_parts > current_parts
        except (ValueError, AttributeError):
            return False

    def update(self, target_version: str | None = None) -> tuple[bool, str]:
        """Perform self-update by pulling from GitHub and reinstalling.

        Args:
            target_version: Specific version to update to (e.g., "v0.2.0").
                          If None, updates to latest.

        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self.install_path:
            return False, "Could not detect installation path. Please reinstall manually."

        if not (self.install_path / ".git").exists():
            return False, "Not a git repository. Please reinstall manually."

        try:
            # Stash any local changes
            subprocess.run(
                ["git", "stash"],
                cwd=self.install_path,
                check=False,
                capture_output=True,
            )

            # Fetch latest changes
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.install_path,
                check=True,
                capture_output=True,
            )

            # Checkout target version or latest
            if target_version:
                ref = target_version if target_version.startswith("v") else f"v{target_version}"
            else:
                ref = "origin/main"

            subprocess.run(
                ["git", "checkout", ref],
                cwd=self.install_path,
                check=True,
                capture_output=True,
            )

            # If on main branch, pull latest
            if not target_version:
                subprocess.run(
                    ["git", "pull", "origin", "main"],
                    cwd=self.install_path,
                    check=True,
                    capture_output=True,
                )

            # Reinstall package
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", str(self.install_path)],
                check=True,
                capture_output=True,
                text=True,
            )

            return True, f"Successfully updated to {target_version or 'latest version'}. Please restart your shell."

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if hasattr(e, "stderr") else str(e)
            return False, f"Update failed: {error_msg}"
        except Exception as e:
            return False, f"Update failed: {e}"

    def get_current_version(self) -> str:
        """Get the current installed version.

        Returns:
            Current version string.
        """
        return self.current_version

    def get_install_info(self) -> dict[str, Any]:
        """Get information about the current installation.

        Returns:
            Dictionary with installation details.
        """
        info: dict[str, Any] = {
            "version": self.current_version,
            "install_path": str(self.install_path) if self.install_path else "Unknown",
            "python_version": sys.version,
            "is_dev_install": self._is_dev_install(),
        }

        if self.install_path and (self.install_path / ".git").exists():
            try:
                # Get current branch
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=self.install_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                info["git_branch"] = result.stdout.strip()

                # Get current commit
                result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=self.install_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                info["git_commit"] = result.stdout.strip()

                # Check if there are uncommitted changes
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=self.install_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                info["has_local_changes"] = bool(result.stdout.strip())

            except subprocess.CalledProcessError:
                pass

        return info

    def _is_dev_install(self) -> bool:
        """Check if this is a development installation.

        Returns:
            True if installed in editable/development mode.
        """
        if not self.install_path:
            return False

        # Check if we're running from the source directory
        source_path = Path(__file__).parent.parent
        return source_path == self.install_path / "src" / "open_orchestrator"
