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

# Try creating venv with pip, fall back to without-pip if ensurepip is unavailable
if $PYTHON_CMD -m venv "$VENV_DIR" 2>/dev/null; then
    echo "  ✓ Virtual environment created at: $VENV_DIR"
else
    echo "  Note: ensurepip unavailable, creating venv without pip..."
    $PYTHON_CMD -m venv --without-pip "$VENV_DIR"
    echo "  ✓ Virtual environment created at: $VENV_DIR"
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Ensure pip is installed (handles --without-pip case)
if ! command -v pip &> /dev/null; then
    echo "  Installing pip via get-pip.py..."
    curl -sS https://bootstrap.pypa.io/get-pip.py | python
fi

pip install --quiet --upgrade pip setuptools wheel
echo "  Installing forked OSWorld in editable mode..."
pip install -e .
echo "  ✓ Dependencies installed"

# Step 4: Initialize VM (if not exists and not skipped)
echo ""
echo -e "${YELLOW}[4/5] Initializing VM...${NC}"

if [ "$SKIP_VM" = true ]; then
    echo "  ⏩ Skipping VM initialization (--skip-vm flag)"
    VM_PATH="$OSWORLD_DIR/vmware_vm_data/Ubuntu0/Ubuntu0.vmx"
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

# Step 5: Configure VM path in .env file
echo ""
echo -e "${YELLOW}[5/5] Configuring ColabGame...${NC}"

ENV_FILE="$COLABGAME_DIR/.env"
# Create .env file if it doesn't exist
if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
    echo "  ✓ Created .env file"
fi

# Check if VM_PATH already exists in .env
if grep -q "^VM_PATH=" "$ENV_FILE"; then
    # Update existing VM_PATH
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s|^VM_PATH=.*|VM_PATH=\"$VM_PATH\"|" "$ENV_FILE"
    else
        # Linux
        sed -i "s|^VM_PATH=.*|VM_PATH=\"$VM_PATH\"|" "$ENV_FILE"
    fi
    echo "  ✓ Updated VM_PATH in .env file"
else
    # Append VM_PATH to .env
    echo "" >> "$ENV_FILE"
    echo "# VM Path - automatically configured by setup_osworld.sh" >> "$ENV_FILE"
    echo "VM_PATH=\"$VM_PATH\"" >> "$ENV_FILE"
    echo "  ✓ Added VM_PATH to .env file"
fi

# Step 6: Summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  OSWorld Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "VM Path: $VM_PATH"
echo ""
echo "Configuration:"
echo "  ✓ VM_PATH has been automatically added to .env file"
echo ""
echo "Next steps:"
echo "  1. Source your .env file (if not already done):"
echo -e "     ${GREEN}export \$(cat .env | xargs)${NC}"
echo "  2. Generate instances:"
echo -e "     ${GREEN}python3 src/instancegenerator.py${NC}"
echo "  3. Run an experiment:"
echo -e "     ${GREEN}python3 -m clem run -g colabgame -m mock${NC}"
echo ""
