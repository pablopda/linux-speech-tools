# Understanding Linux Input Permissions

## Do I need sudo to run talk2claude-faster?

**No.** The tool **never** requires sudo to run. We offer two modes:

## 📋 Default Mode: Clipboard (Recommended)

**How it works:**
- Speech → Text → Clipboard → You paste with Ctrl+V
- **No special permissions needed**
- Works on all Linux systems (Wayland & X11)
- You control when and where to paste

## ⌨️ Advanced Mode: Direct Typing (Optional)

**How it works:**
- Speech → Text → Types directly into active window
- Requires one-time setup (NOT sudo for the app)

### Option 1: uinput Group Method (Recommended)

**One-time setup:**
```bash
# Run our setup script
./scripts/setup/setup-uinput-permissions.sh

# This will:
# 1. Create 'uinput' group
# 2. Add your user to the group
# 3. Configure /dev/uinput permissions
# 4. Require logout/login to take effect
```

After setup, the tool can type directly WITHOUT sudo.

### Option 2: System Daemon (Alternative)

For system-wide deployment, you can run a minimal daemon:

```bash
# Install ydotool
sudo apt install ydotool

# Create systemd service
sudo tee /etc/systemd/system/ydotoold.service << EOF
[Unit]
Description=ydotool daemon
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/ydotoold
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl enable --now ydotoold
```

Now any user can use typing mode through the daemon.

## Security Considerations

### Why does Linux require permissions for typing?

Linux treats input devices (`/dev/uinput`) as privileged resources to prevent:
- Keyloggers
- Malicious input injection
- Unauthorized system control

### What are the trade-offs?

| Mode | Security | Convenience | Setup |
|------|----------|-------------|--------|
| **Clipboard** | ✅ Maximum (user controls paste) | Good | None |
| **uinput group** | ⚠️ Medium (group can inject input) | Excellent | One-time |
| **Daemon** | ⚠️ Medium (daemon has root) | Excellent | One-time |

### Our Recommendation

1. **Start with clipboard mode** - It works immediately with no setup
2. **Add typing mode if needed** - Only for users who want hands-free operation
3. **Never run the main app with sudo** - Use proper permission delegation

## Implementation Details

The tool always defaults to clipboard mode. It still detects whether direct
typing is available, but it uses that path only when requested with `--typing`
or `DICTATION_MODE=typing`:

```python
# Typing capability checks:
1. Check if user is in 'uinput' group
2. Check if ydotoold is running
3. Check if X11 + xdotool is available
4. Use direct typing only when explicitly requested
```

## Comparison with Other Tools

| Tool | Approach | Requires |
|------|----------|----------|
| **talk2claude-faster** | Clipboard default, typing opt-in | Nothing (clipboard) or one-time setup |
| **ydotool** | Daemon with root | sudo for daemon |
| **xdotool** | X11 protocol | X11 only (not Wayland) |
| **AutoKey** | X11 protocol | X11 only |

## For Package Maintainers

When packaging for distributions:

```bash
# Suggest but don't require:
Recommends: wl-clipboard | xclip  # For clipboard mode
Suggests: ydotool                  # For typing mode

# Post-install message:
echo "For direct typing mode, run: /usr/share/talk2claude/setup-uinput-permissions.sh"
```

## Summary

- ✅ **Never requires sudo to run the application**
- ✅ **Clipboard mode works everywhere with no setup**
- ✅ **Typing mode available with proper one-time configuration**
- ✅ **Respects Linux security model**
- ✅ **User stays in control**

This is how modern Linux applications should handle input - safe defaults with opt-in power features!
