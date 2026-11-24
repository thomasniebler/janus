#!/bin/bash
# Janus Startup & Diagnostic Script

# 1. Colors for feedback
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}Starting Janus Diagnostic & Launch Sequence...${NC}"

# 2. Check for UV
if ! command -v uv &> /dev/null; then
    echo -e "${RED}❌ uv not found. Please install it first: https://docs.astral.sh/uv/install/${NC}"
    exit 1
fi
echo -e "${GREEN}✅ uv is installed.${NC}"

# 3. Ensure dependencies are synced, keeping (and upgrading) the optional extras
#    already installed: a plain `uv sync` is exact and would remove them. Same rule
#    as the auto-update (update_service._sync_command): an extra counts as installed
#    when its marker package is.
echo -e "${YELLOW}Syncing dependencies...${NC}"
SYNC_ARGS=(--quiet --inexact)
for pair in desktop:pystray documents:markitdown whatsapp:neonize; do
    if uv pip show --quiet "${pair#*:}" &> /dev/null; then
        SYNC_ARGS+=(--extra "${pair%%:*}")
    fi
done
uv sync "${SYNC_ARGS[@]}"
if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to sync dependencies.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Dependencies are up to date.${NC}"

# 4. Ensure an app-managed Python 3.12 runtime is available
echo -e "${YELLOW}Checking for a managed Python 3.12 runtime...${NC}"
if ! uv python find 3.12 &> /dev/null; then
    echo -e "${YELLOW}⚠️ Python 3.12 not found via uv. Installing managed runtime...${NC}"
    if ! uv python install 3.12; then
        echo -e "${RED}❌ Failed to provision Python 3.12 with uv.${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✅ Python 3.12 runtime is available.${NC}"

# 5. Check for Ollama (Important for Phase 2 Intelligence)
if ! command -v ollama &> /dev/null; then
    echo -e "${YELLOW}⚠️ ollama is not installed. AI features will fail.${NC}"
else
    if ! pgrep -x "ollama" > /dev/null; then
        echo -e "${YELLOW}⚠️ ollama is not running. Attempting to start...${NC}"
        # Start ollama in background if possible, or just warn
        ollama serve > /dev/null 2>&1 &
        sleep 2
    fi
    if pgrep -x "ollama" > /dev/null; then
        echo -e "${GREEN}✅ ollama is running.${NC}"
    else
        echo -e "${RED}❌ ollama failed to start. Local AI will not work.${NC}"
    fi
fi

# 6. Check if Janus is already running
if pgrep -f "janus start" > /dev/null; then
    echo -e "${GREEN}✅ Janus agent is already running in the background.${NC}"
else
    echo -e "${YELLOW}🚀 Starting Janus agent...${NC}"
    # Run janus start in headless server mode. Janus writes logs to the
    # configured persistent log_file_path automatically.
    uv run --python 3.12 janus start --server-mode > /dev/null 2>&1 &

    # Wait a moment to see if it crashes
    sleep 2
    if pgrep -f "janus start" > /dev/null; then
        echo -e "${GREEN}✅ Janus is now running in the background. (Logs: see 'janus config show' for log_file_path)${NC}"
    else
        echo -e "${RED}❌ Janus failed to start. Check the configured log_file_path for details.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✨ All systems go! Janus is at your service.${NC}"
