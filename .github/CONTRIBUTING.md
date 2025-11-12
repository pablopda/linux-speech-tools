# Contributing to Linux Speech Tools

Thank you for your interest in contributing to Linux Speech Tools! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/linux-speech-tools.git`
3. Create a feature branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Test your changes thoroughly
6. Submit a pull request

## Development Environment

### Prerequisites
- Linux distribution (Ubuntu, Debian, Fedora, or similar)
- Python 3.7+
- Git
- Basic audio tools (ffmpeg, espeak-ng)

### Setup
```bash
# Install dependencies
sudo apt-get install python3 python3-pip ffmpeg espeak-ng portaudio19-dev
# or for Fedora:
sudo dnf install python3 python3-pip ffmpeg espeak-ng portaudio-devel

# Install Python packages
pip3 install -r requirements.txt

# Make scripts executable
chmod +x say say-local say-read say-read-es talk2claude
```

## Code Standards

### Shell Scripts
- Use `#!/usr/bin/env bash` shebang
- Enable strict mode: `set -euo pipefail`
- Quote all variables: `"$VARIABLE"`
- Use meaningful variable names
- Add comments for complex logic
- Follow POSIX compatibility where possible

### Python Code
- Follow PEP 8 style guidelines
- Use type hints where appropriate
- Add docstrings for functions and classes
- Handle exceptions gracefully
- Use meaningful variable and function names

### Testing
- Add tests for new functionality
- Ensure existing tests pass
- Test on multiple Linux distributions
- Include both unit tests and integration tests

## Testing Your Changes

### Local Testing
```bash
# Run the test suite
python3 -m pytest tests/ -v

# Test individual commands
./say "Test message"
./say-local "Local test"
python3 say_read.py --test
```

### Distribution Testing
Test your changes on multiple distributions using Docker:

```bash
# Ubuntu 22.04
docker run --rm -v $(pwd):/app -w /app ubuntu:22.04 bash -c "
apt update && apt install -y python3 python3-pip ffmpeg espeak-ng &&
python3 -m pytest tests/ -v
"

# Fedora 39
docker run --rm -v $(pwd):/app -w /app fedora:39 bash -c "
dnf update -y && dnf install -y python3 python3-pip ffmpeg espeak-ng &&
python3 -m pytest tests/ -v
"
```

## Submitting Changes

### Pull Request Process
1. Ensure your code follows the project's coding standards
2. Add or update tests as necessary
3. Update documentation if you're changing functionality
4. Fill out the pull request template completely
5. Ensure all CI checks pass

### Commit Messages
- Use clear, descriptive commit messages
- Start with a verb in imperative mood (e.g., "Add", "Fix", "Update")
- Keep the first line under 50 characters
- Include detailed explanation in the body if needed

Example:
```
Add support for custom voice configurations

- Allow users to specify voice settings in config file
- Add validation for voice parameters
- Update documentation with new configuration options

Fixes #123
```

## Types of Contributions

### Bug Fixes
- Include reproduction steps in your PR description
- Add regression tests if possible
- Reference the issue number in your commit message

### New Features
- Discuss major features in an issue first
- Ensure cross-platform compatibility
- Add comprehensive tests
- Update documentation and help text

### Documentation
- Keep documentation up to date with code changes
- Use clear, concise language
- Include examples where helpful
- Test documentation accuracy

### Performance Improvements
- Include benchmarks showing the improvement
- Ensure changes don't break existing functionality
- Consider memory usage and CPU impact

## Security

### Security Considerations
- Never commit secrets or credentials
- Validate all user inputs
- Use secure defaults
- Follow security best practices for shell scripting

### Reporting Security Issues
Please report security vulnerabilities privately by emailing the maintainers rather than opening a public issue.

## Community Guidelines

### Code of Conduct
- Be respectful and inclusive
- Focus on what's best for the community
- Show empathy towards other community members
- Accept constructive criticism gracefully

### Getting Help
- Check existing issues and documentation first
- Ask questions in GitHub Discussions
- Be specific about your environment and problem
- Provide minimal reproduction cases when reporting bugs

## Release Process

Releases are automated through GitHub Actions:
1. Create a tag following semantic versioning (e.g., `v1.2.3`)
2. Push the tag to trigger the release workflow
3. The CI system will build packages and create a GitHub release
4. Installation scripts will be updated automatically

## Thank You!

Your contributions make Linux Speech Tools better for everyone. We appreciate your time and effort in improving this project!