#!/bin/bash

# launch.sh - Script to launch the web server in a bare-metal environment (non-Docker)

# Default values
VENV_PATH=".venv"
PORT="5000"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -v|--venv)
            VENV_PATH="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  -p, --port PORT       Set the port (default: 5000)"
            echo "  -v, --venv PATH       Set the virtual environment path (default: .venv)"
            echo "  -h, --help            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use -h or --help for usage information"
            exit 1
            ;;
    esac
done

# Activate virtual environment
if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    exit 1
fi

source "$VENV_PATH/bin/activate"

# Launch hypercorn with specified port
hypercorn --bind "0.0.0.0:$PORT" --workers 1 --worker-class asyncio --access-logfile /dev/null --error-logfile - --log-level info app:app 2>&1 | while IFS= read -r line; do
    echo "$line"
    if [[ "$line" == *"Address already in use"* ]] || [[ "$line" == *"Errno 98"* ]]; then
        echo ""
        echo "ERROR: Port $PORT is already in use."
        echo "You can change the port using: $0 --port <PORT_NUMBER>"
        echo "Example: $0 --port 8080"
        exit 1
    fi
done

# Capture the exit code from hypercorn
exit ${PIPESTATUS[0]}