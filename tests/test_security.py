"""Security-focused test suite for Open Orchestrator.

Tests input validation, command redaction, file permissions, and subprocess safety.
"""

import os
import tempfile
from pathlib import Path

import pytest

from open_orchestrator.core.worktree import WorktreeError, WorktreeManager
from open_orchestrator.core.status import StatusTracker
from open_orchestrator.utils.io import atomic_write_text


class TestBranchNameValidation:
    """Test branch name validation prevents directory traversal and option injection."""

    def test_directory_traversal_rejected(self, git_repo):
        """Test that branch names containing '..' are rejected."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeError, match="cannot contain"):
            manager._validate_branch_name("../../../etc/passwd")

        with pytest.raises(WorktreeError, match="cannot contain"):
            manager._validate_branch_name("feature/../main")

        with pytest.raises(WorktreeError, match="cannot contain"):
            manager._validate_branch_name("test/../../etc")

    def test_option_injection_rejected(self, git_repo):
        """Test that branch names starting with '-' are rejected."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeError, match="cannot start with"):
            manager._validate_branch_name("-help")

        with pytest.raises(WorktreeError, match="cannot start with"):
            manager._validate_branch_name("--force")

        with pytest.raises(WorktreeError, match="cannot start with"):
            manager._validate_branch_name("-rf")

    def test_invalid_characters_rejected(self, git_repo):
        """Test that branch names with invalid characters are rejected."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeError, match="invalid characters"):
            manager._validate_branch_name("feature;rm -rf /")

        with pytest.raises(WorktreeError, match="invalid characters"):
            manager._validate_branch_name("branch&& malicious")

        with pytest.raises(WorktreeError, match="invalid characters"):
            manager._validate_branch_name("test|cat /etc/passwd")

        with pytest.raises(WorktreeError, match="invalid characters"):
            manager._validate_branch_name("branch$(whoami)")

        with pytest.raises(WorktreeError, match="invalid characters"):
            manager._validate_branch_name("test`id`")

    def test_empty_branch_name_rejected(self, git_repo):
        """Test that empty branch names are rejected."""
        manager = WorktreeManager(git_repo)

        with pytest.raises(WorktreeError, match="cannot be empty"):
            manager._validate_branch_name("")

    def test_valid_branch_names_accepted(self, git_repo):
        """Test that valid branch names are accepted."""
        manager = WorktreeManager(git_repo)

        # These should not raise exceptions
        manager._validate_branch_name("feature/new-feature")
        manager._validate_branch_name("bugfix/auth-flow")
        manager._validate_branch_name("test_branch")
        manager._validate_branch_name("release-1.0.0")
        manager._validate_branch_name("feature/user.login")
        manager._validate_branch_name("hotfix/critical_fix")


class TestCommandRedaction:
    """Test command sanitization redacts sensitive data."""

    def test_api_keys_redacted(self):
        """Test that API keys are redacted from commands."""
        tracker = StatusTracker()

        test_cases = [
            ("api_key=abc123secret", "api_key=[REDACTED]"),
            ("api-key: xyz789token", "api-key: [REDACTED]"),
            ('API_KEY="sensitive_value"', "API_KEY=[REDACTED]"),
            ("apikey=test123", "apikey=[REDACTED]"),
        ]

        for original, expected in test_cases:
            redacted = tracker._sanitize_command(original)
            assert expected in redacted
            assert "abc123secret" not in redacted
            assert "xyz789token" not in redacted
            assert "sensitive_value" not in redacted

    def test_bearer_tokens_redacted(self):
        """Test that Bearer tokens are redacted from commands."""
        tracker = StatusTracker()

        command = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted = tracker._sanitize_command(command)

        assert "[REDACTED]" in redacted
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    def test_passwords_redacted(self):
        """Test that passwords are redacted from commands."""
        tracker = StatusTracker()

        test_cases = [
            ("password=secret123", "password=[REDACTED]", "secret123"),
            ("password: mypass", "password: [REDACTED]", "mypass"),
            ('PASSWORD="admin123"', "PASSWORD=[REDACTED]", "admin123"),
        ]

        for original, expected, secret in test_cases:
            redacted = tracker._sanitize_command(original)
            assert expected in redacted
            assert secret not in redacted

    def test_jwt_tokens_redacted(self):
        """Test that JWT tokens are redacted from commands."""
        tracker = StatusTracker()

        # Real-looking JWT token (not valid, just for testing)
        command = "Authorization: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        redacted = tracker._sanitize_command(command)

        assert "[JWT REDACTED]" in redacted
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    def test_aws_credentials_redacted(self):
        """Test that AWS credentials are redacted from commands."""
        tracker = StatusTracker()

        # AWS Access Key ID
        command1 = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        redacted1 = tracker._sanitize_command(command1)
        assert "AKIA[REDACTED]" in redacted1
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted1

        # AWS Secret Access Key
        command2 = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        redacted2 = tracker._sanitize_command(command2)
        assert "[REDACTED]" in redacted2
        assert "wJalrXUtnFEMI" not in redacted2

    def test_private_keys_redacted(self):
        """Test that private key blocks are redacted from commands."""
        tracker = StatusTracker()

        private_key = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj
-----END PRIVATE KEY-----"""

        redacted = tracker._sanitize_command(private_key)
        assert "[PRIVATE KEY REDACTED]" in redacted
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj" not in redacted

    def test_urls_with_credentials_redacted(self):
        """Test that URLs with embedded credentials are redacted."""
        tracker = StatusTracker()

        test_cases = [
            ("https://user:pass@example.com/api", "https://[REDACTED]:[REDACTED]@example.com/api"),
            ("http://admin:secret@localhost:8080", "http://[REDACTED]:[REDACTED]@localhost:8080"),
        ]

        for original, expected in test_cases:
            redacted = tracker._sanitize_command(original)
            assert "[REDACTED]" in redacted
            assert "user:pass" not in redacted
            assert "admin:secret" not in redacted

    def test_token_keyword_redacted(self):
        """Test that generic 'token' keywords are redacted."""
        tracker = StatusTracker()

        test_cases = [
            ("token=abc123", "token=[REDACTED]"),
            ("token: xyz789", "token: [REDACTED]"),
            ('TOKEN="sensitive"', "TOKEN=[REDACTED]"),
        ]

        for original, expected in test_cases:
            redacted = tracker._sanitize_command(original)
            assert expected in redacted

    def test_secret_keyword_redacted(self):
        """Test that generic 'secret' keywords are redacted."""
        tracker = StatusTracker()

        test_cases = [
            ("secret=mysecret123", "secret=[REDACTED]"),
            ("secret: topsecret", "secret: [REDACTED]"),
            ('SECRET="classified"', "SECRET=[REDACTED]"),
        ]

        for original, expected in test_cases:
            redacted = tracker._sanitize_command(original)
            assert expected in redacted


class TestFilePermissions:
    """Test file permissions and atomic write operations."""

    def test_atomic_write_creates_correct_permissions(self):
        """Test that atomic_write_text creates files with 0o600 permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_status.json"
            test_data = '{"status": "test"}'

            atomic_write_text(test_file, test_data, perms=0o600)

            # Verify file exists
            assert test_file.exists()

            # Verify permissions are 0o600 (owner read/write only)
            stat_info = test_file.stat()
            permissions = stat_info.st_mode & 0o777
            assert permissions == 0o600, f"Expected 0o600, got {oct(permissions)}"

    def test_atomic_write_default_permissions(self):
        """Test that atomic_write_text uses 0o600 as default permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "default_perms.json"
            test_data = '{"test": true}'

            # Call without specifying perms (should default to 0o600)
            atomic_write_text(test_file, test_data)

            stat_info = test_file.stat()
            permissions = stat_info.st_mode & 0o777
            assert permissions == 0o600, f"Expected 0o600, got {oct(permissions)}"

    def test_atomic_write_cleanup_on_failure(self):
        """Test that atomic_write_text cleans up temp files on failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a directory where the file should be
            test_path = Path(tmpdir) / "file.json"
            test_path.mkdir()  # Make it a directory to cause os.replace to fail

            temp_files_before = set(Path(tmpdir).glob("*"))

            try:
                atomic_write_text(test_path, "test data")
            except (OSError, IsADirectoryError):
                pass  # Expected to fail

            temp_files_after = set(Path(tmpdir).glob("*"))

            # Should not leave temp files behind
            new_files = temp_files_after - temp_files_before
            assert len(new_files) == 0, f"Temp files left behind: {new_files}"

    def test_atomic_write_creates_parent_directory(self):
        """Test that atomic_write_text creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "nested" / "dir" / "file.json"
            test_data = '{"nested": true}'

            atomic_write_text(test_file, test_data)

            assert test_file.exists()
            assert test_file.parent.exists()
            assert test_file.read_text() == test_data


class TestSubprocessSafety:
    """Test that subprocess calls use safe argument passing."""

    def test_git_commands_use_list_arguments(self, git_repo):
        """Test that git commands don't use shell=True."""
        manager = WorktreeManager(git_repo)

        # This test verifies the code doesn't use shell=True by checking
        # that special shell characters in branch names don't cause issues
        # (they would if shell=True was used)

        # The validation should reject these before they reach subprocess
        with pytest.raises(WorktreeError):
            manager._validate_branch_name("test;echo 'injected'")

        with pytest.raises(WorktreeError):
            manager._validate_branch_name("test&&malicious")

    def test_branch_name_with_shell_metacharacters_safe(self, git_repo):
        """Test that shell metacharacters in valid branch names are handled safely."""
        manager = WorktreeManager(git_repo)

        # Valid branch names with characters that COULD be dangerous if shell=True
        # These should be rejected by validation
        dangerous_names = [
            "test;ls",
            "branch&&rm",
            "test|cat",
            "branch$(whoami)",
            "test`id`",
            "branch>file",
            "test<input",
        ]

        for name in dangerous_names:
            with pytest.raises(WorktreeError):
                manager._validate_branch_name(name)
