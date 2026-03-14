# Open Orchestrator Demo — fully mocked for clean recording
# Run: vhs assets/demo.tape

Output assets/demo.gif
Set FontSize 14
Set Width 1200
Set Height 700
Set Framerate 15
Set TypingSpeed 50ms
Set Padding 20
Set WindowBar Colorful
Set WindowBarSize 40

# Unified dark palette — matches Textual dark theme
Set Theme { "name": "OWT", "black": "#26233a", "red": "#eb6f92", "green": "#e0def4", "yellow": "#e0def4", "blue": "#9ccfd8", "magenta": "#c4a7e7", "cyan": "#9ccfd8", "white": "#e0def4", "brightBlack": "#6e6a86", "brightRed": "#eb6f92", "brightGreen": "#e0def4", "brightYellow": "#e0def4", "brightBlue": "#9ccfd8", "brightMagenta": "#c4a7e7", "brightCyan": "#9ccfd8", "brightWhite": "#e0def4", "background": "#191724", "foreground": "#e0def4", "selectionBackground": "#26233a", "cursorColor": "#e0def4" }

# Hidden setup: alias owt to mock script
Hide
Type@0 "cd /Users/pedrolopes/dev/projects/open-orchestrator"
Enter
Sleep 300ms
Type@0 "alias owt='python3 assets/demo_mock_owt.py'"
Enter
Sleep 300ms
Type@0 "clear"
Enter
Sleep 300ms
Show

# ── Show commands ───────────────────────────────────────────────────────────
Type "owt --help"
Sleep 300ms
Enter
Sleep 3s

# ── Create worktrees ────────────────────────────────────────────────────────
Type "owt new Add user authentication with JWT"
Sleep 300ms
Enter
Sleep 2s

Type "owt new Write API documentation"
Sleep 300ms
Enter
Sleep 2s

Type "owt new Add integration tests for payments"
Sleep 300ms
Enter
Sleep 2s

# ── List worktrees ──────────────────────────────────────────────────────────
Type "owt list"
Sleep 300ms
Enter
Sleep 3s

# ── Send to agents ──────────────────────────────────────────────────────────
Type 'owt send add-user-auth-jwt "Focus on refresh tokens"'
Sleep 300ms
Enter
Sleep 1.5s

Type 'owt send --all "Run tests before committing"'
Sleep 300ms
Enter
Sleep 2s

# ── Launch Switchboard ──────────────────────────────────────────────────────
Type "owt"
Sleep 300ms
Enter
Sleep 3s

# Navigate between cards
Right
Sleep 800ms
Right
Sleep 800ms
Right
Sleep 800ms
Left
Sleep 800ms
Left
Sleep 800ms
Left
Sleep 800ms

Sleep 2s

# Quit
Type "q"
Sleep 1s
