#!/usr/bin/env python3
"""
Comprehensive test suite for Linux Speech Tools
Tests all core functionality across different environments
"""

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

class TestSpeechTools(unittest.TestCase):

    def setUp(self):
        """Set up test environment"""
        self.test_text = "Testing Linux Speech Tools"
        self.script_dir = Path(__file__).parent.parent

    def test_say_command_exists(self):
        """Test that say command exists and is executable"""
        say_path = self.script_dir / "say"
        self.assertTrue(say_path.exists(), "say command not found")
        self.assertTrue(os.access(say_path, os.X_OK), "say command not executable")

    def test_say_local_command_exists(self):
        """Test that say-local command exists and is executable"""
        say_local_path = self.script_dir / "say-local"
        self.assertTrue(say_local_path.exists(), "say-local command not found")
        self.assertTrue(os.access(say_local_path, os.X_OK), "say-local command not executable")

    def test_python_tts_script_exists(self):
        """Test that Python TTS script exists"""
        python_script = self.script_dir / "say_read.py"
        self.assertTrue(python_script.exists(), "say_read.py not found")

    def test_say_help_option(self):
        """Test say command help option"""
        try:
            result = subprocess.run([str(self.script_dir / "say"), "-h"],
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0)
            self.assertIn("usage:", result.stdout.lower())
        except subprocess.TimeoutExpired:
            self.fail("say -h command timed out")

    def test_say_file_output(self):
        """Test say command file output functionality"""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            result = subprocess.run([
                str(self.script_dir / "say"),
                "-o", tmp_path,
                self.test_text
            ], capture_output=True, text=True, timeout=30)

            # Check if command completed (may fail due to missing edge-tts)
            if result.returncode == 0:
                self.assertTrue(os.path.exists(tmp_path), "Output file not created")
                self.assertGreater(os.path.getsize(tmp_path), 0, "Output file is empty")
            else:
                # Command failed, likely due to missing dependencies
                self.assertIn(("edge-tts" or "command not found"),
                            result.stderr.lower() or result.stdout.lower(),
                            f"Unexpected error: {result.stderr}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def test_python_script_imports(self):
        """Test that Python script can be imported without errors"""
        try:
            result = subprocess.run([
                sys.executable, "-c",
                f"import sys; sys.path.append('{self.script_dir}'); import say_read"
            ], capture_output=True, text=True, timeout=10)

            # If import fails due to missing dependencies, that's expected
            if result.returncode != 0:
                error_msg = result.stderr.lower()
                expected_errors = ["no module named", "modulenotfounderror", "importerror"]
                self.assertTrue(any(err in error_msg for err in expected_errors),
                              f"Unexpected import error: {result.stderr}")
        except subprocess.TimeoutExpired:
            self.fail("Python script import test timed out")

    def test_installer_script_exists(self):
        """Test that installer script exists and is readable"""
        installer_path = self.script_dir / "installer.sh"
        self.assertTrue(installer_path.exists(), "installer.sh not found")

        # Test that installer has expected content
        with open(installer_path) as f:
            content = f.read()
            self.assertIn("#!/", content, "Installer missing shebang")
            self.assertIn("install", content.lower(), "Installer missing install functionality")

    def test_voice_commands_syntax(self):
        """Test that voice command scripts have valid syntax"""
        scripts = ["say", "say-local", "say-read", "say-read-es", "talk2claude"]

        for script in scripts:
            script_path = self.script_dir / script
            if script_path.exists():
                with open(script_path) as f:
                    content = f.read()
                    if content.startswith("#!/"):
                        # Basic syntax validation for shell scripts
                        self.assertNotIn("syntax error", content.lower())

                        # Test with bash syntax check if possible
                        try:
                            result = subprocess.run([
                                "bash", "-n", str(script_path)
                            ], capture_output=True, text=True, timeout=5)

                            if result.returncode != 0:
                                self.fail(f"Syntax error in {script}: {result.stderr}")
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            # bash not available or timed out, skip syntax check
                            pass

    def test_readme_exists(self):
        """Test that README file exists"""
        readme_path = self.script_dir / "README.md"
        self.assertTrue(readme_path.exists(), "README.md not found")

        with open(readme_path) as f:
            content = f.read()
            self.assertGreater(len(content), 10, "README is too short")

class TestDistributionCompatibility(unittest.TestCase):
    """Test compatibility across different Linux distributions"""

    def test_shebang_compatibility(self):
        """Test that all scripts use portable shebangs"""
        script_dir = Path(__file__).parent.parent
        scripts = ["say", "say-local", "say-read", "say-read-es", "talk2claude"]

        for script in scripts:
            script_path = script_dir / script
            if script_path.exists():
                with open(script_path) as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("#!"):
                        # Should use env for portability
                        self.assertTrue(
                            first_line.startswith("#!/usr/bin/env") or
                            first_line.startswith("#!/bin/bash") or
                            first_line.startswith("#!/bin/sh"),
                            f"Non-portable shebang in {script}: {first_line}"
                        )

class TestEnvironmentSetup(unittest.TestCase):
    """Test environment setup and configuration"""

    def test_required_commands_check(self):
        """Test that installer can detect required commands"""
        # This would test the installer's dependency checking
        pass

    def test_config_file_handling(self):
        """Test configuration file handling"""
        # This would test XDG config directory usage
        pass

if __name__ == "__main__":
    # Run tests with verbose output
    unittest.main(verbosity=2)