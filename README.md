# ColabGame

ColabGame is a clemgame benchmark for evaluating LLM agents in computer-use scenarios. Agents interact with an Ubuntu virtual machine environment (via OSWorld) to complete realistic computer-based tasks such as debugging code, processing data, editing images, and synthesizing research.

The game supports both single-agent and multi-agent topologies, allowing evaluation of collaborative agent behaviors across different communication structures.

## OSWorld Environment Setup

OSWorld is a benchmark environment for evaluating multimodal agents in real computer environments. It provides scalable, reproducible virtual machines (Ubuntu and Windows) where agents can interact with actual operating systems, applications, and files. OSWorld supports realistic computer tasks including file operations, web browsing, and application use, with a comprehensive evaluation framework for agent capabilities. For more information, see the [official website](https://os-world.github.io/), [paper](https://arxiv.org/abs/2404.07972), and [documentation](https://timothyxxx.github.io/OSWorld/).

ColabGame uses OSWorld as an external computer substrate to enable LLM agents to interact with real computer environments. Agents can execute shell commands and scripts, manipulate files and directories, use applications (browsers, editors, office tools), and complete realistic computer-based tasks. Currently, ColabGame supports the Ubuntu Linux environment only.

### Prerequisites

- **Python 3.12+** installed
- **~25GB disk space** (VM image + dependencies)
- **Git** installed

> **Important:** Clone OSWorld **outside** of the colabgame project directory (e.g., as a sibling folder).

### Option A: Automated Setup (Recommended)

After installing VMware (see [Step 2](#step-2-install-vmware) below), run the setup script:

```bash
bash setup_osworld.sh [--osworld-dir PATH] [--skip-vm]
```

**Arguments:**

- `--osworld-dir PATH`: Specify OSWorld clone location (default: `../OSWorld`)
- `--skip-vm`: Skip VM initialization (for testing)

The script checks prerequisites, clones OSWorld, installs dependencies, and downloads the Ubuntu VM (~20GB).

### Option B: Manual Setup

#### Step 1: Clone OSWorld

```bash
git clone https://github.com/Nid989/OSWorld
cd OSWorld
pip install -e .
```

#### Step 2: Install VMware

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

#### Step 3: Initialize VM

Run quickstart to download and setup the VM (~20GB):

```bash
cd OSWorld
python quickstart.py
```

The VM will be saved to: `./vmware_vm_data/Ubuntu0/Ubuntu0.vmx`

### VM Credentials

- **Username:** `user`
- **Password:** `password`

## Task Categories

ColabGame experiments span five task categories across three difficulty levels:

| Category                    | Description                                                     |
| --------------------------- | --------------------------------------------------------------- |
| **Debugging & Refactoring** | Fix syntax errors, complete logic, update configurations        |
| **Tabular Data Reporting**  | Transfer, aggregate, and calculate data across files            |
| **Image Processing**        | Insert, resize, and caption images in documents                 |
| **Research Synthesis**      | Extract web information, summarize content, integrate downloads |
| **Workflow Orchestration**  | Gather and organize information across applications             |

Additionally, ColabGame supports OSWorld benchmark tasks (`chrome`, `gimp`, `libreoffice_writer`, `os`).

> Note: Not all instances are available for OSWorld tasks; we sample a subset from the original dataset.

## Agent Topologies

ColabGame supports four agent topologies:

- **Single** – A single agent with full environment access
- **Star** – Hub agent coordinates with spoke agents (centralized); spoke agents has environment access
- **Mesh** – All agents connected (decentralized); agents take turns with environment access
- **Blackboard** – Shared context model; agents contribute via a common workspace (round-robin fashion)

Topology configurations are defined in [resources/topologies](resources/topologies/).

## Prompts

Prompt templates are located under [resources/prompts](resources/prompts/).

## Instance Generation

Experiments are configured in [resources/config.yaml](resources/config.yaml). Generate instances by running:

```bash
uv run python src/instancegenerator.py
```

Instances are written to [in/instances.json](in/instances.json).

## Run

Run experiments using the `clem` CLI:

```bash
# Run with a specific model
python clem run -g colabgame -m <model_name>

# Run with mock model for testing
python clem run -g colabgame -m mock
```

To transcribe interactions into readable formats:

```bash
python clem transcribe -g colabgame
```

To score completed episodes:

```bash
python clem score -g colabgame
```

To generate evaluation tables:

```bash
python clem eval
```

## Scoring

**Episode-level scores**

- **Bench Score**: Success \* 100.
  - **Success**: Evaluation of the final state of the desktop environment and instance-defined criteria determines agent(s) goal completion. The game must not abort.
- **Request Statistics**: Total, parsed, and violated requests per agent.
