#!/usr/bin/env python3
"""
Inject worktree context into Claude Code prompts.

This hook script detects if we're inside a git worktree and
adds context about the current worktree to help Claude Code
understand the development context.
"""

import os
import subprocess
import sys


def get_worktree_context() -> str:
    """
    Get context about the current worktree.

    Returns:
        A context string if in a non-main worktree, empty string otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )

        cwd = os.getcwd()
        worktrees = result.stdout.strip().split("\n\n")

        # Find which worktree we're in
        for block in worktrees:
            if not block.strip():
                continue

            lines = block.strip().split("\n")
            wt_path = ""
            wt_branch = ""

            for line in lines:
                if line.startswith("worktree "):
                    wt_path = line.replace("worktree ", "")
                elif line.startswith("branch "):
                    wt_branch = line.replace("branch refs/heads/", "")

            # Check if we're in this worktree (but not the main one)
            if wt_path and cwd.startswith(wt_path):
                # Skip if this is the main worktree (first in list usually)
                if worktrees.index(block) == 0:
                    continue

                worktree_name = os.path.basename(wt_path)
                if wt_branch:
                    return f"[Worktree: {worktree_name} | Branch: {wt_branch}]"
                return f"[Worktree: {worktree_name}]"

    except subprocess.CalledProcessError:
        # Not a git repository or git not available
        pass
    except subprocess.TimeoutExpired:
        # Git command timed out
        pass
    except Exception:
        # Any other error - fail silently
        pass

    return ""


def main() -> None:
    """Main entry point."""
    context = get_worktree_context()
    if context:
        print(context)
    sys.exit(0)


if __name__ == "__main__":
    main()
