#!/bin/bash
# GNOME Shell restart methods for Wayland sessions

echo "🔄 GNOME Shell Restart Options for Wayland"
echo "=========================================="
echo ""
echo "Choose restart method:"
echo ""
echo "1) Quick Logout/Login (Recommended)"
echo "   └─ Preserves all applications, fastest method"
echo ""
echo "2) Restart Session"
echo "   └─ Logs out completely, closes all apps"
echo ""
echo "3) D-Bus Restart (Advanced)"
echo "   └─ Attempts to restart shell via D-Bus"
echo ""

read -p "Enter your choice (1-3): " choice

case $choice in
    1)
        echo "🔄 Starting quick logout/login..."
        echo "This will:"
        echo "  1. Save your session"
        echo "  2. Log you out"
        echo "  3. Auto-login back (if enabled)"
        echo ""
        read -p "Continue? (y/N): " confirm
        if [[ $confirm =~ ^[Yy]$ ]]; then
            gnome-session-quit --logout --no-prompt
        fi
        ;;
    2)
        echo "🔄 Restarting GNOME session..."
        echo "This will close all applications!"
        echo ""
        read -p "Are you sure? (y/N): " confirm
        if [[ $confirm =~ ^[Yy]$ ]]; then
            gnome-session-quit --logout
        fi
        ;;
    3)
        echo "🔄 Attempting D-Bus restart..."
        if busctl --user call org.gnome.Shell /org/gnome/Shell org.gnome.Shell Eval s 'Meta.restart("Restarting...")' 2>/dev/null; then
            echo "✅ D-Bus restart initiated"
        else
            echo "❌ D-Bus restart failed. Try option 1 or 2."
        fi
        ;;
    *)
        echo "❌ Invalid choice. Run the script again."
        ;;
esac