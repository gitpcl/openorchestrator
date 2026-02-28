#!/usr/bin/env python3
"""
Generate demo GIF for Open Orchestrator.

This script creates an animated GIF showcasing the parallel agents workflow
by capturing screenshots of the TUI at various states and combining them
into an optimized GIF file.

Usage:
    python scripts/generate_demo.py
    # Or via Makefile:
    make demo-gif

Requirements:
    - pillow
    - cairosvg (optional, for SVG conversion)
    - VHS (optional, for high-quality recordings)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

if TYPE_CHECKING:
    pass

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
OUTPUT_GIF = ASSETS_DIR / "demo.gif"


def check_vhs_available() -> bool:
    """Check if VHS (Charm) is available for recording."""
    try:
        result = subprocess.run(
            ["vhs", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def generate_with_vhs() -> bool:
    """Generate demo GIF using VHS tape file."""
    tape_file = ASSETS_DIR / "demo.tape"
    if not tape_file.exists():
        print(f"Error: VHS tape file not found at {tape_file}")
        return False

    print("Generating demo GIF using VHS...")
    try:
        result = subprocess.run(
            ["vhs", str(tape_file)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print(f"Demo GIF generated at {OUTPUT_GIF}")
            return True
        print(f"VHS failed: {result.stderr}")
        return False
    except subprocess.SubprocessError as e:
        print(f"VHS error: {e}")
        return False


def create_mock_worktrees() -> list:
    """Create mock worktree data for demo screenshots."""
    # Import here to avoid circular imports
    from open_orchestrator.models.status import AIActivityStatus, TokenUsage, WorktreeAIStatus
    from open_orchestrator.models.worktree_info import WorktreeInfo

    worktrees = [
        WorktreeInfo(
            path=Path("/Users/demo/project/feature-auth"),
            branch="feature/auth",
            head_commit="abc1234",
            is_main=False,
        ),
        WorktreeInfo(
            path=Path("/Users/demo/project/feature-api"),
            branch="feature/api",
            head_commit="def5678",
            is_main=False,
        ),
        WorktreeInfo(
            path=Path("/Users/demo/project/main"),
            branch="main",
            head_commit="9ab0123",
            is_main=True,
        ),
    ]

    statuses = [
        WorktreeAIStatus(
            worktree_name="feature-auth",
            worktree_path="/Users/demo/project/feature-auth",
            branch="feature/auth",
            activity_status=AIActivityStatus.WORKING.value,
            current_task="Implementing JWT authentication",
            token_usage=TokenUsage(input_tokens=15000, output_tokens=8000),
            recent_commands=["Writing auth service", "Adding tests"],
            updated_at=datetime.now(),
        ),
        WorktreeAIStatus(
            worktree_name="feature-api",
            worktree_path="/Users/demo/project/feature-api",
            branch="feature/api",
            activity_status=AIActivityStatus.WORKING.value,
            current_task="Creating REST endpoints",
            token_usage=TokenUsage(input_tokens=12000, output_tokens=6000),
            recent_commands=["Adding routes", "Updating schema"],
            updated_at=datetime.now(),
        ),
        WorktreeAIStatus(
            worktree_name="main",
            worktree_path="/Users/demo/project/main",
            branch="main",
            activity_status=AIActivityStatus.IDLE.value,
            current_task="",
            token_usage=TokenUsage(input_tokens=0, output_tokens=0),
            recent_commands=[],
            updated_at=datetime.now(),
        ),
    ]

    return worktrees, statuses


async def capture_tui_screenshots() -> list[Path]:
    """
    Capture TUI screenshots using Textual's test runner.

    Returns a list of screenshot file paths.
    """
    # Import TUI components
    from open_orchestrator.core.status import StatusTracker
    from open_orchestrator.core.worktree import WorktreeManager
    from open_orchestrator.tui.app import OrchestratorApp

    screenshots = []
    worktrees, statuses = create_mock_worktrees()

    # Create mock dependencies
    mock_tracker = Mock(spec=StatusTracker)
    mock_tracker.cleanup_orphans = Mock()
    mock_tracker.get_summary = Mock()

    # Setup status returns
    def get_status(name: str):
        for s in statuses:
            if s.worktree_name == name:
                return s
        return None

    mock_tracker.get_status = Mock(side_effect=get_status)

    mock_wt = Mock(spec=WorktreeManager)
    mock_wt.list_all = Mock(return_value=worktrees)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create app instance
        app = OrchestratorApp(
            status_tracker=mock_tracker,
            wt_manager=mock_wt,
        )

        # Capture screenshots at different states
        async with app.run_test(size=(120, 40)) as pilot:
            # Frame 1: Initial TUI view
            await pilot.pause(delay=0.5)
            frame1 = tmppath / "frame_001.svg"
            app.save_screenshot(str(frame1))
            screenshots.append(frame1)

            # Frame 2: After navigation down
            await pilot.press("j")
            await pilot.pause(delay=0.3)
            frame2 = tmppath / "frame_002.svg"
            app.save_screenshot(str(frame2))
            screenshots.append(frame2)

            # Frame 3: Navigate down again
            await pilot.press("j")
            await pilot.pause(delay=0.3)
            frame3 = tmppath / "frame_003.svg"
            app.save_screenshot(str(frame3))
            screenshots.append(frame3)

            # Frame 4: Navigate back up
            await pilot.press("k")
            await pilot.pause(delay=0.3)
            frame4 = tmppath / "frame_004.svg"
            app.save_screenshot(str(frame4))
            screenshots.append(frame4)

        # Return paths (files will be processed before tmpdir cleanup)
        return screenshots


def svg_to_png(svg_path: Path, png_path: Path, width: int = 1200) -> bool:
    """Convert SVG to PNG using cairosvg."""
    try:
        import cairosvg

        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=width,
        )
        return True
    except ImportError:
        print("cairosvg not available, trying alternative...")
        # Try rsvg-convert (common on Linux/macOS)
        try:
            subprocess.run(
                ["rsvg-convert", "-w", str(width), "-o", str(png_path), str(svg_path)],
                check=True,
                capture_output=True,
            )
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            print(f"Could not convert {svg_path} to PNG")
            return False


def create_placeholder_gif() -> bool:
    """
    Create a placeholder GIF with instructions.

    This is used when proper recording tools are not available.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not available. Install with: pip install pillow")
        return False

    # Create frames
    frames = []
    width, height = 1200, 800
    bg_color = (30, 30, 46)  # Catppuccin Mocha base
    text_color = (205, 214, 244)  # Catppuccin Mocha text
    accent_color = (137, 180, 250)  # Catppuccin Mocha blue

    texts = [
        [
            "Open Orchestrator",
            "",
            "Parallel AI Agent Orchestration",
            "for Git Worktrees",
        ],
        [
            "Feature Highlights:",
            "",
            "- Interactive TUI",
            "- Multiple AI agents in parallel",
            "- Live status monitoring",
            "- Token tracking & cost analysis",
        ],
        [
            "Quick Start:",
            "",
            "$ owt create feature/auth",
            "$ owt create feature/api",
            "$ owt   # Opens TUI dashboard",
        ],
        [
            "Keyboard Navigation:",
            "",
            "n - New worktree",
            "d - Delete worktree",
            "j/k - Navigate",
            "a - A/B comparison",
            "q - Quit",
        ],
    ]

    for text_lines in texts:
        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Try to use a nice font, fallback to default
        try:
            # Try common monospace fonts
            for font_name in ["JetBrains Mono", "Fira Code", "Menlo", "Monaco", "DejaVu Sans Mono"]:
                try:
                    title_font = ImageFont.truetype(font_name, 48)
                    body_font = ImageFont.truetype(font_name, 28)
                    break
                except OSError:
                    continue
            else:
                title_font = ImageFont.load_default()
                body_font = ImageFont.load_default()
        except Exception:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()

        # Draw border
        draw.rectangle(
            [(20, 20), (width - 20, height - 20)],
            outline=accent_color,
            width=2,
        )

        # Draw text
        y = 150
        for i, line in enumerate(text_lines):
            font = title_font if i == 0 else body_font
            color = accent_color if i == 0 else text_color

            # Center the text
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
                text_width = bbox[2] - bbox[0]
            except AttributeError:
                # Older Pillow versions
                text_width = len(line) * 15

            x = (width - text_width) // 2
            draw.text((x, y), line, fill=color, font=font)
            y += 60 if i == 0 else 45

        frames.append(img)

    # Duplicate frames for longer viewing
    extended_frames = []
    for frame in frames:
        # Show each frame for ~2 seconds at 10fps
        extended_frames.extend([frame] * 20)

    # Save as GIF
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    extended_frames[0].save(
        OUTPUT_GIF,
        save_all=True,
        append_images=extended_frames[1:],
        duration=100,  # 100ms per frame = 10fps
        loop=0,
    )

    # Optimize with gifsicle if available
    optimize_gif(OUTPUT_GIF)

    print(f"Placeholder demo GIF created at {OUTPUT_GIF}")
    return True


def optimize_gif(gif_path: Path) -> None:
    """Optimize GIF using gifsicle if available."""
    try:
        result = subprocess.run(
            [
                "gifsicle",
                "-O3",
                "--colors",
                "256",
                "-o",
                str(gif_path),
                str(gif_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("GIF optimized with gifsicle")
    except (subprocess.SubprocessError, FileNotFoundError):
        print("gifsicle not available, skipping optimization")


def main() -> int:
    """Main entry point for demo generation."""
    print("=" * 60)
    print("Open Orchestrator Demo GIF Generator")
    print("=" * 60)
    print()

    # Ensure assets directory exists
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    # Try VHS first (best quality)
    if check_vhs_available():
        print("VHS detected, using for high-quality recording...")
        if generate_with_vhs():
            size = OUTPUT_GIF.stat().st_size / (1024 * 1024)
            print(f"Success! GIF size: {size:.2f}MB")
            if size > 5:
                print("Warning: GIF exceeds 5MB limit. Consider optimizing.")
            return 0

    # Fallback: Create placeholder GIF with Pillow
    print("VHS not available, creating placeholder demo GIF...")
    if create_placeholder_gif():
        size = OUTPUT_GIF.stat().st_size / (1024 * 1024)
        print(f"Success! GIF size: {size:.2f}MB")
        print()
        print("Note: This is a placeholder GIF. For a proper demo recording:")
        print("  1. Install VHS: brew install charmbracelet/tap/vhs")
        print("  2. Run: make demo-gif")
        return 0

    print("Failed to generate demo GIF")
    return 1


if __name__ == "__main__":
    sys.exit(main())
