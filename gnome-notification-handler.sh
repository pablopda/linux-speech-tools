#!/usr/bin/env bash
# GNOME Notification Action Handler for Speech Reader
# Handles play/pause/stop actions from notification buttons

set -euo pipefail

# D-Bus interface for speech reader control
DBUS_SERVICE="org.gnome.SpeechTools.Reader"
DBUS_OBJECT="/org/gnome/SpeechTools/Reader"
DBUS_INTERFACE="org.gnome.SpeechTools.Reader"

# Helper function for D-Bus calls
call_reader_dbus() {
    local method="$1"
    dbus-send --session --print-reply \
        --dest="$DBUS_SERVICE" \
        "$DBUS_OBJECT" \
        "${DBUS_INTERFACE}.${method}" \
        2>/dev/null || return 1
}

# Show notification with message
show_notification() {
    local title="$1"
    local message="$2"
    local icon="${3:-audio-volume-medium-symbolic}"

    notify-send \
        --icon="$icon" \
        --app-name="Speech Reader" \
        --urgency=low \
        "$title" "$message"
}

# Handle notification actions
handle_action() {
    local action="$1"

    case "$action" in
        "pause")
            echo "⏸️ Pausing reading..."
            if call_reader_dbus "pause_reading"; then
                show_notification "⏸️ Reading Paused" "Audio playback has been paused" "media-pause-symbolic"
            else
                show_notification "❌ Error" "Failed to pause reading" "dialog-error-symbolic"
            fi
            ;;

        "resume")
            echo "▶️ Resuming reading..."
            if call_reader_dbus "resume_reading"; then
                show_notification "▶️ Reading Resumed" "Audio playback has been resumed" "media-play-symbolic"
            else
                show_notification "❌ Error" "Failed to resume reading" "dialog-error-symbolic"
            fi
            ;;

        "stop")
            echo "⏹️ Stopping reading..."
            if call_reader_dbus "stop_reading"; then
                show_notification "⏹️ Reading Stopped" "Audio playback has been stopped" "media-stop-symbolic"
            else
                show_notification "❌ Error" "Failed to stop reading" "dialog-error-symbolic"
            fi
            ;;

        "status")
            # Check if reader service is running
            if call_reader_dbus "start_reading" --type=method_call --print-reply 2>/dev/null; then
                show_notification "📖 Speech Reader" "Service is running and ready" "audio-volume-high-symbolic"
            else
                show_notification "📖 Speech Reader" "Service is not running" "audio-volume-muted-symbolic"
            fi
            ;;

        *)
            echo "❌ Unknown action: $action"
            show_notification "❌ Unknown Action" "Action '$action' is not recognized" "dialog-error-symbolic"
            ;;
    esac
}

# Check if service is available
check_service() {
    if ! command -v dbus-send &> /dev/null; then
        echo "❌ Error: dbus-send is not available"
        show_notification "❌ Error" "D-Bus tools are not installed" "dialog-error-symbolic"
        return 1
    fi

    return 0
}

# Install notification actions into GNOME
setup_notification_actions() {
    echo "🔧 Setting up GNOME notification actions for Speech Reader..."

    # Create desktop file for actions
    local desktop_file="$HOME/.local/share/applications/speech-reader-actions.desktop"

    cat > "$desktop_file" << 'EOF'
[Desktop Entry]
Type=Application
Name=Speech Reader Actions
Exec=/usr/bin/env bash -c "exec \"$0\" handle_action \"$1\""
NoDisplay=true
StartupNotify=false
EOF

    echo "✅ Notification actions configured"
    echo "   Actions available: pause, resume, stop, status"
}

# Show usage information
show_usage() {
    cat << EOF
GNOME Notification Action Handler for Speech Reader

Usage:
  $(basename "$0") <action>

Actions:
  pause       Pause current reading session
  resume      Resume paused reading session
  stop        Stop current reading session
  status      Check service status
  setup       Setup notification actions

Examples:
  $(basename "$0") pause      # Pause reading
  $(basename "$0") resume     # Resume reading
  $(basename "$0") stop       # Stop reading
  $(basename "$0") setup      # Setup system integration

This script is typically called by GNOME notification action buttons,
but can also be used manually from the command line.
EOF
}

# Main function
main() {
    if [ $# -eq 0 ]; then
        show_usage
        exit 1
    fi

    local command="$1"
    shift

    # Check system requirements
    if ! check_service; then
        exit 1
    fi

    case "$command" in
        "handle_action"|"action")
            if [ $# -eq 0 ]; then
                echo "❌ Error: Action required"
                exit 1
            fi
            handle_action "$1"
            ;;

        "setup")
            setup_notification_actions
            ;;

        "pause"|"resume"|"stop"|"status")
            handle_action "$command"
            ;;

        "help"|"--help"|"-h")
            show_usage
            ;;

        *)
            echo "❌ Error: Unknown command '$command'"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@"