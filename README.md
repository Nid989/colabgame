# ColabGame

ColabGame is a clemgame benchmark for evaluating LLM agents in computer-use scenarios. Agents interact with an Ubuntu virtual machine environment (via OSWorld) to complete realistic computer-based tasks such as debugging code, processing data, editing images, and synthesizing research.

The game supports both single-agent and multi-agent topologies, allowing evaluation of collaborative agent behaviors across different communication structures.

## Overview

### What is ColabGame?

ColabGame is a benchmark designed to evaluate the capabilities of Large Language Model (LLM) agents in realistic computer-use scenarios. Unlike traditional text-based benchmarks, ColabGame tests agents' ability to interact with actual operating systems, applications, and files within a virtual machine environment.

### Key Features

- **Realistic Computer Tasks**: Agents complete real-world tasks including debugging code, processing data, editing images, and synthesizing research
- **Multi-Agent Support**: Evaluate collaborative behaviors through various agent topologies (Single, Star, Mesh, Blackboard)
- **OSWorld Integration**: Leverages OSWorld's scalable, reproducible Ubuntu virtual machine environment
- **Comprehensive Evaluation**: Episode-level scoring with success metrics and request statistics
- **Flexible Task Categories**: Five task categories across three difficulty levels, plus support for OSWorld benchmark tasks

### Architecture

ColabGame uses OSWorld as an external computer substrate to enable LLM agents to interact with real computer environments. Agents can:

- Execute shell commands and scripts
- Manipulate files and directories
- Use applications (browsers, editors, office tools)
- Complete realistic computer-based tasks

Currently, ColabGame supports the Ubuntu Linux environment only.

## Getting Started

### Prerequisites

Before installing ColabGame, ensure you have:

- **Python 3.12+** installed
- **~25GB disk space** (VM image + dependencies)
- **Git** installed
- **VMware Workstation Pro** or **VMware Fusion 13** (for macOS with Apple chips)

> **Important:** Clone OSWorld **outside** of the colabgame project directory (e.g., as a sibling folder).

### Quick Start Guide

1. **Install VMware** (see [Installation](#installation) section below)
2. **Run the automated setup script**:
   ```bash
   bash setup_osworld.sh
   ```
3. **Configure environment variables** (PYTHONPATH and S3 credentials)
4. **Generate instances**:
   ```bash
   python3 src/instancegenerator.py
   ```
5. **Run your first experiment**:
   ```bash
   python3 -m clem run -g colabgame -m mock
   ```

## Installation

### OSWorld Environment Setup

OSWorld is a benchmark environment for evaluating multimodal agents in real computer environments. It provides scalable, reproducible virtual machines (Ubuntu and Windows) where agents can interact with actual operating systems, applications, and files. OSWorld supports realistic computer tasks including file operations, web browsing, and application use, with a comprehensive evaluation framework for agent capabilities.

For more information, see the [official website](https://os-world.github.io/), [paper](https://arxiv.org/abs/2404.07972), and [documentation](https://timothyxxx.github.io/OSWorld/).

#### Option A: Automated Setup (Recommended)

After installing VMware (see [Step 2](#step-2-install-vmware) below), run the setup script:

```bash
bash setup_osworld.sh [--osworld-dir PATH] [--skip-vm]
```

**Arguments:**

- `--osworld-dir PATH`: Specify OSWorld clone location (default: `../OSWorld`)
- `--skip-vm`: Skip VM initialization (for testing)

The script checks prerequisites, clones OSWorld, installs dependencies, and downloads the Ubuntu VM (~20GB).

#### Option B: Manual Setup

##### Step 1: Clone OSWorld

```bash
git clone https://github.com/Nid989/OSWorld
cd OSWorld
pip install -e .
```

##### Step 2: Install VMware

For non-virtualized systems (desktop, laptop, bare metal machine), use VMware.

1. Install [VMware Workstation Pro](https://www.vmware.com/products/workstation-pro/workstation-pro-evaluation.html) (for systems with Apple Chips, install [VMware Fusion 13](https://support.broadcom.com/group/ecx/productdownloads?subfamily=VMware%20Fusion&freeDownloads=true))

   > **Note:** Broadcom account required. License key may be requested during installation.

   Installation references:
   - [Linux](https://docs.vmware.com/en/VMware-Workstation-Pro/17/com.vmware.ws.using.doc/GUID-1F5B1F14-A586-4A56-83FA-2E7D8333D5CA.html)
   - [Windows](https://docs.vmware.com/en/VMware-Workstation-Pro/17/com.vmware.ws.using.doc/GUID-F5A7B3CB-9141-458B-A256-E0C3EA805AAA.html)
   - [macOS (Apple chips)](https://docs.vmware.com/en/VMware-Fusion/13/com.vmware.fusion.using.doc/GUID-ACC3A019-93D3-442C-A34E-F7755DF6733B.html)

2. Verify installation:
   ```bash
   vmrun -T ws list    # Windows/Linux
   vmrun -T fusion list  # macOS
   ```
   You should see the message showing current running virtual machines.

> **Note:** [VirtualBox](https://www.virtualbox.org/) is supported as an alternative to VMware. However, parallelism and macOS on Apple chips may not be well-supported.

##### Step 3: Initialize VM

Run quickstart to download and setup the VM (~20GB):

```bash
cd OSWorld
python quickstart.py
```

The VM will be saved to: `./vmware_vm_data/Ubuntu0/Ubuntu0.vmx`

#### Environment Configuration

##### PYTHONPATH Setup

Add the OSWorld repository path to your `PYTHONPATH`. You can do this in two ways:

**Option 1: Export directly in your shell**

```bash
export PYTHONPATH=/path/to/OSWorld:$PYTHONPATH
```

**Option 2: Add to `.env` file**

Add the following to your `.env` file in the colabgame project root:

```bash
PYTHONPATH=/path/to/OSWorld
```

Then source the `.env` file:

```bash
export $(cat .env | xargs)
```

##### S3 Configuration

Configure the following S3 environment variables in your `.env` file. These are necessary for:

- Syncing screenshots from the environment to local results for transcribing
- Uploading ground truth contents for evaluating the environment state at the end of an instance run

```bash
AWS_ACCESS_KEY_ID=your_access_key_id
AWS_SECRET_ACCESS_KEY=your_secret_access_key
AWS_REGION=your_aws_region
S3_BUCKET_NAME=your_bucket_name
```

Replace the placeholder values with your actual AWS credentials and S3 bucket information.

#### VM Credentials

- **Username:** `user`
- **Password:** `password`

## Configuration

### Task Categories

ColabGame experiments span five task categories across three difficulty levels:

| Category                    | Description                                                     |
| --------------------------- | --------------------------------------------------------------- |
| **Debugging & Refactoring** | Fix syntax errors, complete logic, update configurations        |
| **Tabular Data Reporting**  | Transfer, aggregate, and calculate data across files            |
| **Image Processing**        | Insert, resize, and caption images in documents                 |
| **Research Synthesis**      | Extract web information, summarize content, integrate downloads |
| **Workflow Orchestration**  | Gather and organize information across applications             |

Additionally, ColabGame supports OSWorld benchmark tasks (`chrome`, `gimp`, `libreoffice_writer`, `os`).

> **Note:** Not all instances are available for OSWorld tasks; we sample a subset from the original dataset.

### Agent Topologies

ColabGame supports four agent topologies for evaluating different collaboration patterns:

- **Single** – A single agent with full environment access
- **Star** – Hub agent coordinates with spoke agents (centralized); spoke agents have environment access
- **Mesh** – All agents connected (decentralized); agents take turns with environment access
- **Blackboard** – Shared context model; agents contribute via a common workspace (round-robin fashion)

Topology configurations are defined in [resources/topologies](resources/topologies/).

### Prompts

Prompt templates define how agents interact with tasks and the environment. These templates are customizable and located under [resources/prompts](resources/prompts/).

The prompt system supports:

- Task-specific instructions
- Agent role definitions
- Communication protocols for multi-agent topologies
- Environment interaction guidelines

### Instance Generation

Experiments are configured in [resources/config.yaml](resources/config.yaml). This configuration file defines:

- Task categories and difficulty levels
- Agent topologies to evaluate
- Model configurations
- Evaluation parameters

Generate instances by running:

```bash
uv run python src/instancegenerator.py
```

Instances are written to [in/instances.json](in/instances.json). Each instance contains:

- Task description and requirements
- Initial environment state
- Success criteria
- Agent configuration

## Usage

### Running Experiments

Run experiments using the `clem` CLI:

```bash
# Run with a specific model
python clem run -g colabgame -m <model_name>

# Run with mock model for testing
python clem run -g colabgame -m mock
```

### Transcribing Results

To transcribe interactions into readable formats:

```bash
python clem transcribe -g colabgame
```

### Scoring Episodes

To score completed episodes:

```bash
python clem score -g colabgame
```

### Generating Evaluation Tables

To generate evaluation tables:

```bash
python clem eval
```

## Scoring Metrics

### Episode-Level Scores

ColabGame uses the following metrics to evaluate agent performance:

#### Bench Score

**Formula:** Success × 100

The primary metric for task completion. A binary score where:

- **100**: Task completed successfully
- **0**: Task failed or aborted

#### Success Criteria

Success is determined by:

1. **Final Environment State**: Evaluation of the desktop environment state at episode completion
2. **Instance-Defined Criteria**: Task-specific requirements (e.g., file creation, correct output, application state)
3. **No Abort Condition**: The game must complete without errors or timeouts

#### Request Statistics

Tracks agent behavior and compliance:

- **Total Requests**: Number of actions attempted by each agent
- **Parsed Requests**: Successfully formatted and executable requests
- **Violated Requests**: Actions that violate environment constraints or communication rules

These statistics help identify:

- Agent efficiency (fewer requests for same outcome)
- Instruction-following capability (high parse rate)
- Constraint adherence (low violation rate)

### Multi-Agent Metrics

For multi-agent topologies, additional metrics include:

- **Collaboration Efficiency**: Success rate relative to communication overhead
- **Agent Contribution**: Individual agent impact on task completion
- **Communication Patterns**: Message flow and coordination effectiveness

## References

- **OSWorld Official Website**: [https://os-world.github.io/](https://os-world.github.io/)
- **OSWorld Paper**: [https://arxiv.org/abs/2404.07972](https://arxiv.org/abs/2404.07972)
- **OSWorld Documentation**: [https://timothyxxx.github.io/OSWorld/](https://timothyxxx.github.io/OSWorld/)
- **OSWorld Repository (Fork)**: [https://github.com/Nid989/OSWorld](https://github.com/Nid989/OSWorld)
