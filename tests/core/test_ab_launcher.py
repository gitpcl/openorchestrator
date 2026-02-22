"""Tests for A/B agent launcher."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from open_orchestrator.config import AITool
from open_orchestrator.core.ab_launcher import ABLauncher, ABLauncherError, ToolNotInstalledError
from open_orchestrator.models.ab_workspace import ABWorkspace, ABWorkspaceStore
from open_orchestrator.models.worktree_info import WorktreeInfo


@pytest.fixture
def ab_store_path(temp_directory: Path) -> Path:
    """Create temporary A/B workspace store path."""
    store_dir = temp_directory / ".open-orchestrator"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "ab_workspaces.json"


@pytest.fixture
def mock_worktree_manager():
    """Create a mock WorktreeManager for testing."""
    manager = MagicMock()

    # Mock create method to return WorktreeInfo
    def create_worktree(branch: str, base_branch: str | None = None, **kwargs):
        return WorktreeInfo(
            path=Path(f"/tmp/worktrees/{branch}"),
            branch=branch,
            head_commit="abc1234",
            is_main=False,
            is_detached=False,
        )

    manager.create = Mock(side_effect=create_worktree)
    return manager


@pytest.fixture
def mock_tmux_manager():
    """Create a mock TmuxManager for testing."""
    manager = MagicMock()

    # Mock server and session
    mock_session = MagicMock()
    mock_session.name = "owt-ab-test"
    mock_session.id = "$1"

    mock_window = MagicMock()

    # Create two mock panes
    mock_pane_0 = MagicMock()
    mock_pane_0.set_title = MagicMock()

    mock_pane_1 = MagicMock()
    mock_pane_1.set_title = MagicMock()

    mock_window.panes = [mock_pane_0, mock_pane_1]
    mock_window.split = MagicMock(return_value=mock_pane_1)
    mock_window.select_layout = MagicMock()
    mock_window.set_window_option = MagicMock()

    mock_session.active_window = mock_window

    # Mock server.new_session to return the mock session
    manager.server = MagicMock()
    manager.server.new_session = MagicMock(return_value=mock_session)

    # Mock _start_ai_tool_in_pane
    manager._start_ai_tool_in_pane = MagicMock()

    # Mock send_keys_to_pane
    manager.send_keys_to_pane = MagicMock()

    return manager


@pytest.fixture
def ab_launcher(git_repo: Path, ab_store_path: Path, mock_worktree_manager, mock_tmux_manager):
    """Create ABLauncher instance with mocked dependencies."""
    with (
        patch("open_orchestrator.core.ab_launcher.WorktreeManager", return_value=mock_worktree_manager),
        patch("open_orchestrator.core.ab_launcher.TmuxManager", return_value=mock_tmux_manager),
    ):
        launcher = ABLauncher(repo_path=git_repo, store_path=ab_store_path)
        yield launcher


class TestABLauncher:
    """Test suite for ABLauncher class."""

    def test_init(self, git_repo: Path, ab_store_path: Path):
        """Test ABLauncher initialization."""
        with (
            patch("open_orchestrator.core.ab_launcher.WorktreeManager") as mock_wt,
            patch("open_orchestrator.core.ab_launcher.TmuxManager") as mock_tmux,
        ):
            launcher = ABLauncher(repo_path=git_repo, store_path=ab_store_path)

            assert launcher.store_path == ab_store_path
            assert isinstance(launcher.store, ABWorkspaceStore)
            mock_wt.assert_called_once_with(repo_path=git_repo)
            mock_tmux.assert_called_once()

    def test_validate_tools_success(self, ab_launcher: ABLauncher):
        """Test tool validation with both tools installed."""
        with patch.object(AITool, "is_installed", return_value=True):
            # Should not raise
            ab_launcher._validate_tools(AITool.CLAUDE, AITool.OPENCODE)

    def test_validate_tools_first_tool_not_installed(self, ab_launcher: ABLauncher):
        """Test tool validation fails when first tool not installed."""

        def mock_is_installed(tool: AITool) -> bool:
            return tool != AITool.CLAUDE

        with (
            patch.object(AITool, "is_installed", side_effect=mock_is_installed),
            patch.object(AITool, "get_install_hint", return_value="Install hint"),
        ):
            with pytest.raises(ToolNotInstalledError, match="AI tool 'claude' is not installed"):
                ab_launcher._validate_tools(AITool.CLAUDE, AITool.OPENCODE)

    def test_validate_tools_second_tool_not_installed(self, ab_launcher: ABLauncher):
        """Test tool validation fails when second tool not installed."""

        def mock_is_installed(tool: AITool) -> bool:
            return tool != AITool.OPENCODE

        with (
            patch.object(AITool, "is_installed", side_effect=mock_is_installed),
            patch.object(AITool, "get_install_hint", return_value="Install hint"),
        ):
            with pytest.raises(ToolNotInstalledError, match="AI tool 'opencode' is not installed"):
                ab_launcher._validate_tools(AITool.CLAUDE, AITool.OPENCODE)

    def test_generate_workspace_id(self, ab_launcher: ABLauncher):
        """Test workspace ID generation."""
        workspace_id = ab_launcher._generate_workspace_id("feature/authentication")

        assert workspace_id.startswith("ab-feature-authentication-")
        assert len(workspace_id) > len("ab-feature-authentication-")

    def test_generate_worktree_name(self, ab_launcher: ABLauncher):
        """Test worktree name generation with tool suffix."""
        name_claude = ab_launcher._generate_worktree_name("feature/auth", AITool.CLAUDE)
        name_opencode = ab_launcher._generate_worktree_name("feature/auth", AITool.OPENCODE)

        assert name_claude == "feature/auth-claude"
        assert name_opencode == "feature/auth-opencode"

    def test_generate_session_name(self, ab_launcher: ABLauncher):
        """Test tmux session name generation."""
        session_name = ab_launcher._generate_session_name("ab-test-20240201-120000")

        assert session_name == "owt-ab-test-20240201-120000"

    def test_launch_happy_path(self, ab_launcher: ABLauncher, mock_worktree_manager, mock_tmux_manager):
        """Test successful A/B workspace launch."""
        with (
            patch.object(AITool, "is_installed", return_value=True),
            patch.object(ab_launcher, "_save_store") as mock_save,
        ):
            workspace = ab_launcher.launch(
                branch="feature/test",
                tool_a=AITool.CLAUDE,
                tool_b=AITool.OPENCODE,
                base_branch="main",
                initial_prompt=None,
            )

            # Verify workspace metadata
            assert workspace.branch == "feature/test"
            assert workspace.worktree_a == "feature/test-claude"
            assert workspace.worktree_b == "feature/test-opencode"
            assert workspace.tool_a == AITool.CLAUDE
            assert workspace.tool_b == AITool.OPENCODE
            assert workspace.initial_prompt is None

            # Verify worktrees were created
            assert mock_worktree_manager.create.call_count == 2
            calls = mock_worktree_manager.create.call_args_list
            assert calls[0][1]["branch"] == "feature/test-claude"
            assert calls[0][1]["base_branch"] == "main"
            assert calls[1][1]["branch"] == "feature/test-opencode"
            assert calls[1][1]["base_branch"] == "main"

            # Verify tmux session was created
            mock_tmux_manager.server.new_session.assert_called_once()

            # Verify AI tools were started in panes
            assert mock_tmux_manager._start_ai_tool_in_pane.call_count == 2

            # Verify store was saved
            mock_save.assert_called_once()

    def test_launch_with_prompt(self, ab_launcher: ABLauncher, mock_worktree_manager, mock_tmux_manager):
        """Test A/B workspace launch with initial prompt."""
        initial_prompt = "Implement JWT authentication"

        with (
            patch.object(AITool, "is_installed", return_value=True),
            patch.object(ab_launcher, "_save_store"),
        ):
            workspace = ab_launcher.launch(
                branch="feature/auth",
                tool_a=AITool.CLAUDE,
                tool_b=AITool.OPENCODE,
                initial_prompt=initial_prompt,
            )

            # Verify prompt was stored
            assert workspace.initial_prompt == initial_prompt

            # Verify prompt was dispatched to both panes
            assert mock_tmux_manager.send_keys_to_pane.call_count == 2
            calls = mock_tmux_manager.send_keys_to_pane.call_args_list

            # Check both calls sent the same prompt
            assert calls[0][1]["keys"] == initial_prompt
            assert calls[1][1]["keys"] == initial_prompt

            # Check pane indices
            assert calls[0][1]["pane_index"] == 0
            assert calls[1][1]["pane_index"] == 1

    def test_launch_tool_not_installed(self, ab_launcher: ABLauncher):
        """Test launch fails when tool is not installed."""
        with patch.object(AITool, "is_installed", return_value=False):
            with pytest.raises(ToolNotInstalledError):
                ab_launcher.launch(
                    branch="feature/test",
                    tool_a=AITool.CLAUDE,
                    tool_b=AITool.OPENCODE,
                )

    def test_launch_worktree_creation_fails(self, ab_launcher: ABLauncher, mock_worktree_manager):
        """Test launch handles worktree creation failure."""
        mock_worktree_manager.create.side_effect = Exception("Failed to create worktree")

        with patch.object(AITool, "is_installed", return_value=True):
            with pytest.raises(ABLauncherError, match="Failed to create worktrees"):
                ab_launcher.launch(
                    branch="feature/test",
                    tool_a=AITool.CLAUDE,
                    tool_b=AITool.OPENCODE,
                )

    def test_launch_tmux_creation_fails(self, ab_launcher: ABLauncher, mock_tmux_manager):
        """Test launch handles tmux session creation failure."""
        mock_tmux_manager.server.new_session.side_effect = Exception("Failed to create session")

        with (
            patch.object(AITool, "is_installed", return_value=True),
            patch.object(ab_launcher, "worktree_manager"),
        ):
            with pytest.raises(ABLauncherError, match="Failed to create tmux session"):
                ab_launcher.launch(
                    branch="feature/test",
                    tool_a=AITool.CLAUDE,
                    tool_b=AITool.OPENCODE,
                )

    def test_launch_prompt_dispatch_fails(self, ab_launcher: ABLauncher, mock_tmux_manager):
        """Test launch handles prompt dispatch failure."""
        mock_tmux_manager.send_keys_to_pane.side_effect = Exception("Failed to send keys")

        with (
            patch.object(AITool, "is_installed", return_value=True),
            patch.object(ab_launcher, "_save_store"),
        ):
            with pytest.raises(ABLauncherError, match="Failed to dispatch initial prompt"):
                ab_launcher.launch(
                    branch="feature/test",
                    tool_a=AITool.CLAUDE,
                    tool_b=AITool.OPENCODE,
                    initial_prompt="Test prompt",
                )

    def test_create_split_session(self, ab_launcher: ABLauncher, mock_tmux_manager):
        """Test split tmux session creation."""
        session = ab_launcher._create_split_session(
            session_name="owt-ab-test",
            worktree_a_path="/tmp/worktree-a",
            worktree_b_path="/tmp/worktree-b",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            worktree_a_name="feature-auth-claude",
            worktree_b_name="feature-auth-opencode",
        )

        # Verify session was created
        mock_tmux_manager.server.new_session.assert_called_once_with(
            session_name="owt-ab-test",
            start_directory="/tmp/worktree-a",
            window_name="ab-comparison",
            attach=False,
        )

        # Verify window was split
        session.active_window.split.assert_called_once()

        # Verify layout was set
        session.active_window.select_layout.assert_called_once_with("even-horizontal")

        # Verify pane titles were set
        panes = session.active_window.panes
        assert panes[0].set_title.call_count == 1
        assert panes[1].set_title.call_count == 1

        # Verify AI tools were started
        assert mock_tmux_manager._start_ai_tool_in_pane.call_count == 2

    def test_dispatch_prompt(self, ab_launcher: ABLauncher, mock_tmux_manager):
        """Test prompt dispatch to both panes."""
        ab_launcher._dispatch_prompt("owt-ab-test", "Implement authentication")

        # Verify send_keys_to_pane was called twice
        assert mock_tmux_manager.send_keys_to_pane.call_count == 2

        calls = mock_tmux_manager.send_keys_to_pane.call_args_list

        # Verify first pane
        assert calls[0][1]["session_name"] == "owt-ab-test"
        assert calls[0][1]["keys"] == "Implement authentication"
        assert calls[0][1]["pane_index"] == 0

        # Verify second pane
        assert calls[1][1]["session_name"] == "owt-ab-test"
        assert calls[1][1]["keys"] == "Implement authentication"
        assert calls[1][1]["pane_index"] == 1

    def test_get_workspace(self, ab_launcher: ABLauncher):
        """Test getting workspace by ID."""
        # Create and store a workspace
        workspace = ABWorkspace(
            id="test-workspace",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        ab_launcher.store.add_workspace(workspace)

        # Retrieve it
        retrieved = ab_launcher.get_workspace("test-workspace")
        assert retrieved is not None
        assert retrieved.id == "test-workspace"
        assert retrieved.branch == "feature/test"

    def test_get_workspace_not_found(self, ab_launcher: ABLauncher):
        """Test getting non-existent workspace."""
        result = ab_launcher.get_workspace("nonexistent")
        assert result is None

    def test_list_workspaces(self, ab_launcher: ABLauncher):
        """Test listing all workspaces."""
        # Add two workspaces
        workspace1 = ABWorkspace(
            id="workspace-1",
            branch="feature/test1",
            worktree_a="feature-test1-claude",
            worktree_b="feature-test1-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test1",
        )

        workspace2 = ABWorkspace(
            id="workspace-2",
            branch="feature/test2",
            worktree_a="feature-test2-claude",
            worktree_b="feature-test2-droid",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.DROID,
            tmux_session="owt-ab-test2",
        )

        ab_launcher.store.add_workspace(workspace1)
        ab_launcher.store.add_workspace(workspace2)

        # List workspaces
        workspaces = ab_launcher.list_workspaces()
        assert len(workspaces) == 2
        assert workspace1 in workspaces
        assert workspace2 in workspaces

    def test_find_by_worktree(self, ab_launcher: ABLauncher):
        """Test finding workspace by worktree name."""
        workspace = ABWorkspace(
            id="test-workspace",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        ab_launcher.store.add_workspace(workspace)

        # Find by worktree_a
        found_a = ab_launcher.find_by_worktree("feature-test-claude")
        assert found_a is not None
        assert found_a.id == "test-workspace"

        # Find by worktree_b
        found_b = ab_launcher.find_by_worktree("feature-test-opencode")
        assert found_b is not None
        assert found_b.id == "test-workspace"

        # Find by non-existent worktree
        not_found = ab_launcher.find_by_worktree("nonexistent")
        assert not_found is None

    def test_store_persistence(self, ab_launcher: ABLauncher, ab_store_path: Path):
        """Test workspace store persistence."""
        workspace = ABWorkspace(
            id="test-workspace",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        ab_launcher.store.add_workspace(workspace)
        ab_launcher._save_store()

        # Verify file was created
        assert ab_store_path.exists()

        # Load in new launcher instance
        with (
            patch("open_orchestrator.core.ab_launcher.WorktreeManager"),
            patch("open_orchestrator.core.ab_launcher.TmuxManager"),
        ):
            new_launcher = ABLauncher(store_path=ab_store_path)
            workspaces = new_launcher.list_workspaces()

            assert len(workspaces) == 1
            assert workspaces[0].id == "test-workspace"


class TestABWorkspaceModel:
    """Test suite for ABWorkspace data model."""

    def test_workspace_creation(self):
        """Test ABWorkspace creation with required fields."""
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        assert workspace.id == "test-id"
        assert workspace.branch == "feature/test"
        assert workspace.worktree_a == "feature-test-claude"
        assert workspace.worktree_b == "feature-test-opencode"
        assert workspace.tool_a == AITool.CLAUDE
        assert workspace.tool_b == AITool.OPENCODE
        assert workspace.tmux_session == "owt-ab-test"
        assert workspace.initial_prompt is None
        assert isinstance(workspace.created_at, datetime)

    def test_workspace_with_prompt(self):
        """Test ABWorkspace creation with initial prompt."""
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
            initial_prompt="Implement authentication",
        )

        assert workspace.initial_prompt == "Implement authentication"

    def test_get_worktrees(self):
        """Test get_worktrees helper method."""
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        worktrees = workspace.get_worktrees()
        assert worktrees == ("feature-test-claude", "feature-test-opencode")

    def test_get_tools(self):
        """Test get_tools helper method."""
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        tools = workspace.get_tools()
        assert tools == (AITool.CLAUDE, AITool.OPENCODE)


class TestABWorkspaceStore:
    """Test suite for ABWorkspaceStore."""

    def test_add_workspace(self):
        """Test adding workspace to store."""
        store = ABWorkspaceStore()
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        store.add_workspace(workspace)
        assert "test-id" in store.workspaces
        assert store.workspaces["test-id"] == workspace

    def test_add_duplicate_workspace(self):
        """Test adding duplicate workspace raises error."""
        store = ABWorkspaceStore()
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        store.add_workspace(workspace)

        with pytest.raises(ValueError, match="A/B workspace 'test-id' already exists"):
            store.add_workspace(workspace)

    def test_get_workspace(self):
        """Test getting workspace from store."""
        store = ABWorkspaceStore()
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        store.add_workspace(workspace)
        retrieved = store.get_workspace("test-id")

        assert retrieved is not None
        assert retrieved.id == "test-id"

    def test_remove_workspace(self):
        """Test removing workspace from store."""
        store = ABWorkspaceStore()
        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        store.add_workspace(workspace)
        store.remove_workspace("test-id")

        assert "test-id" not in store.workspaces

    def test_remove_nonexistent_workspace(self):
        """Test removing nonexistent workspace raises error."""
        store = ABWorkspaceStore()

        with pytest.raises(KeyError, match="A/B workspace 'nonexistent' not found"):
            store.remove_workspace("nonexistent")

    def test_list_workspaces(self):
        """Test listing all workspaces."""
        store = ABWorkspaceStore()

        workspace1 = ABWorkspace(
            id="test-1",
            branch="feature/test1",
            worktree_a="feature-test1-claude",
            worktree_b="feature-test1-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test1",
        )

        workspace2 = ABWorkspace(
            id="test-2",
            branch="feature/test2",
            worktree_a="feature-test2-claude",
            worktree_b="feature-test2-droid",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.DROID,
            tmux_session="owt-ab-test2",
        )

        store.add_workspace(workspace1)
        store.add_workspace(workspace2)

        workspaces = store.list_workspaces()
        assert len(workspaces) == 2
        assert workspace1 in workspaces
        assert workspace2 in workspaces

    def test_find_by_worktree(self):
        """Test finding workspace by worktree name."""
        store = ABWorkspaceStore()

        workspace = ABWorkspace(
            id="test-id",
            branch="feature/test",
            worktree_a="feature-test-claude",
            worktree_b="feature-test-opencode",
            tool_a=AITool.CLAUDE,
            tool_b=AITool.OPENCODE,
            tmux_session="owt-ab-test",
        )

        store.add_workspace(workspace)

        found_a = store.find_by_worktree("feature-test-claude")
        found_b = store.find_by_worktree("feature-test-opencode")
        not_found = store.find_by_worktree("nonexistent")

        assert found_a is not None
        assert found_a.id == "test-id"
        assert found_b is not None
        assert found_b.id == "test-id"
        assert not_found is None
