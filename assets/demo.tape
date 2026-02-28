# Open Orchestrator Demo Recording
# VHS (Charm) tape file for recording the hero workflow demonstration
#
# Install VHS: brew install charmbracelet/tap/vhs
# Run: vhs assets/demo.tape
#
# This tape demonstrates:
# 1. Launching the TUI with `owt`
# 2. Creating first worktree (feature/auth) with 'n'
# 3. Creating second worktree (feature/api) with 'n'
# 4. Switching to Dashboard view with Tab
# 5. Showing parallel agents with live status updates

# Output configuration
Output assets/demo.gif
Set FontSize 14
Set Width 1200
Set Height 800
Set Theme "Catppuccin Mocha"
Set Framerate 12
Set TypingSpeed 50ms
Set Padding 20

# Window title
Set WindowBar Colorful
Set WindowBarSize 40

# Recording starts
Hide
Type "cd $(pwd)"
Enter
Sleep 500ms
Show

# Step 1: Launch the TUI
Type "owt"
Sleep 1s
Enter
Sleep 2s

# Show the TUI main screen with worktree list
# Wait for UI to render
Sleep 1500ms

# Step 2: Create first worktree (feature/auth)
Type "n"
Sleep 1s
# In real TUI, a dialog would appear - simulate input
Type "feature/auth"
Enter
Sleep 2s

# Step 3: Create second worktree (feature/api)
Type "n"
Sleep 1s
Type "feature/api"
Enter
Sleep 2s

# Step 4: Navigate and show the worktree list
# Use j/k to navigate between worktrees
Type "j"
Sleep 500ms
Type "j"
Sleep 500ms
Type "k"
Sleep 500ms

# Step 5: Switch to Dashboard view (Tab key)
# Note: This assumes Tab cycles views in the TUI
Tab
Sleep 2s

# Show live status updates happening
Sleep 3s

# Final pause before exit
Sleep 2s

# Exit with 'q'
Type "q"
Sleep 1s
