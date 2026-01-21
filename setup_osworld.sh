#!/bin/bash
# OSWorld Setup Script
# Usage: bash setup_osworld.sh [--osworld-dir PATH]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default OSWorld directory (sibling to colabgame)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLABGAME_DIR="$SCRIPT_DIR"
OSWORLD_DIR="${COLABGAME_DIR}/../OSWorld"
SKIP_VM=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --osworld-dir)
            OSWORLD_DIR="$2"
            shift 2
            ;;
        --skip-vm)
            SKIP_VM=true
            shift
            ;;
        --help)
            echo "Usage: bash setup_osworld.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --osworld-dir PATH   Directory to clone/find OSWorld (default: ../OSWorld)"
            echo "  --skip-vm            Skip VM initialization (for testing)"
            echo "  --help               Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OSWorld Setup Script${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/5] Checking prerequisites...${NC}"

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 not found. Please install Python 3.12+${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  ✓ Python $PYTHON_VERSION found"

# Check Git
if ! command -v git &> /dev/null; then
    echo -e "${RED}ERROR: git not found. Please install git.${NC}"
    exit 1
fi
echo "  ✓ Git found"

# Check vmrun
if command -v vmrun &> /dev/null; then
    echo "  ✓ VMware vmrun found"
else
    echo -e "${RED}ERROR: vmrun not found. Please install VMware first.${NC}"
    echo ""
    echo "  Install VMware:"
    echo "  - macOS: https://support.broadcom.com/group/ecx/productdownloads?subfamily=VMware+Fusion"
    echo "  - Windows/Linux: https://www.vmware.com/products/workstation-pro/workstation-pro-evaluation.html"
    echo ""
    echo "  See SUPERVISOR_SETUP.md for detailed instructions."
    exit 1
fi

# Step 2: Clone or find OSWorld
echo ""
echo -e "${YELLOW}[2/5] Setting up OSWorld...${NC}"

# Expand ~ and get absolute path (macOS compatible)
OSWORLD_DIR="${OSWORLD_DIR/#\~/$HOME}"
OSWORLD_DIR="$(cd "$(dirname "$OSWORLD_DIR")" 2>/dev/null && pwd)/$(basename "$OSWORLD_DIR")" || OSWORLD_DIR="$OSWORLD_DIR"

if [ -d "$OSWORLD_DIR/.git" ]; then
    echo "  ✓ OSWorld already exists at: $OSWORLD_DIR"
else
    echo "  Cloning OSWorld to: $OSWORLD_DIR"
    git clone https://github.com/Nid989/OSWorld "$OSWORLD_DIR"
    echo "  ✓ OSWorld cloned"
fi

# Step 3: Create temporary virtual environment and install dependencies
echo ""
echo -e "${YELLOW}[3/5] Creating virtual environment and installing dependencies...${NC}"

cd "$OSWORLD_DIR"

# Create temporary virtual environment for OSWorld setup
VENV_DIR="$OSWORLD_DIR/.venv_setup"

# Determine Python command (require 3.12+ per OSWorld requirements)
if command -v python3.12 &> /dev/null; then
    PYTHON_CMD="python3.12"
    echo "  Using python3.12"
elif command -v python3 &> /dev/null; then
    # Check if python3 is 3.12+
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
    PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)
    
    if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 12 ]; then
        PYTHON_CMD="python3"
        echo "  Using python3 ($PY_VERSION)"
    else
        echo -e "${RED}ERROR: Python 3.12+ required (found $PY_VERSION)${NC}"
        echo "  OSWorld requires Python >=3.12"
        exit 1
    fi
else
    echo -e "${RED}ERROR: Python 3.12+ not found${NC}"
    echo "  Please install Python 3.12 or higher"
    exit 1
fi

# Remove existing venv if it exists (to avoid Python version conflicts)
if [ -d "$VENV_DIR" ]; then
    echo "  Removing existing virtual environment..."
    rm -rf "$VENV_DIR"
fi

$PYTHON_CMD -m venv "$VENV_DIR"
echo "  ✓ Virtual environment created at: $VENV_DIR"

# Activate and install
source "$VENV_DIR/bin/activate"
pip install --quiet --upgrade pip setuptools wheel
echo "  Installing forked OSWorld in editable mode..."
pip install -e .
echo "  ✓ Dependencies installed"

# Step 4: Initialize VM (if not exists and not skipped)
echo ""
echo -e "${YELLOW}[4/5] Initializing VM...${NC}"

if [ "$SKIP_VM" = true ]; then
    echo "  ⏩ Skipping VM initialization (--skip-vm flag)"
else
    VM_PATH="$OSWORLD_DIR/vmware_vm_data/Ubuntu0/Ubuntu0.vmx"

    if [ -f "$VM_PATH" ]; then
        echo "  ✓ VM already exists at: $VM_PATH"
    else
        echo "  Downloading and setting up VM (~20GB)..."
        echo "  This may take 10-30 minutes depending on your connection."
        python quickstart.py
        echo "  ✓ VM initialized"
    fi
fi

# Deactivate virtual environment
deactivate

# Step 5: Summary
echo ""
echo -e "${YELLOW}[5/5] Setup complete!${NC}"
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OSWorld Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "VM Path: $VM_PATH"
echo ""
echo "Next steps for ColabGame:"
echo "  1. Update path_to_vm in src/utils/constants.py:"
echo -e "     ${GREEN}\"path_to_vm\": \"$VM_PATH\"${NC}"
echo "  2. Run: uv run clem run -g colabgame -m model_name"
echo ""
