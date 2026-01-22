import argparse
import copy
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

import yaml
from dotenv import load_dotenv

from clemcore.clemgame.instances import GameInstanceGenerator

# Path to the configuration file
CONFIG_PATH = Path(__file__).parent.parent / "resources" / "config.yaml"


@dataclass
class ExperimentSpec:
    """Specification for a single experiment to be generated."""

    name: str
    topology: str
    category: str
    observation_type: str
    level: int | None = None
    task_type: str | None = None  # For OSWorld experiments


@dataclass
class OSWorldSession:
    """Simple session-like container for OSWorld task information."""

    successful_tasks: list = field(default_factory=list)
    failed_tasks: list = field(default_factory=list)


class ColabGameInstanceGenerator(GameInstanceGenerator):
    """
    Generates instances for the computer game with different observation types.
    Uses a declarative configuration to reduce redundancy in the config file.
    """

    def __init__(self, path: str):
        super().__init__(path)
        self._osworld_tasks = {}
        self._load_config()
        self._load_osworld_tasks()

    def _load_config(self) -> None:
        """Load and validate configuration from yaml file."""
        load_dotenv()
        if not os.getenv("S3_BUCKET_NAME"):
            os.environ["S3_BUCKET_NAME"] = "thesis-bhavsar"

        with open(CONFIG_PATH, "r") as f:
            self.config = yaml.safe_load(f)

        self._validate_config()

    def _validate_config(self) -> None:
        """Validate configuration structure."""
        required = ["system"]
        for section in required:
            if section not in self.config:
                raise ValueError(f"Missing required section: {section}")

        system_keys = [
            "screen_width",
            "screen_height",
            "max_rounds",
            "max_transitions_per_round",
            "player_consecutive_violation_limit",
            "player_total_violation_limit",
        ]
        for key in system_keys:
            if key not in self.config["system"]:
                raise ValueError(f"System config missing required key: {key}")
        # vm_path is optional if VM_PATH environment variable is set
        if "vm_path" not in self.config["system"] and not os.getenv("VM_PATH"):
            raise ValueError("System config missing required key: vm_path (or set VM_PATH environment variable)")

    def _load_osworld_tasks(self) -> None:
        """Load OSWorld tasks from osworld_subset.json."""
        osworld_path = "in/osworld_subset.json"
        try:
            with open(osworld_path, "r") as f:
                osworld_data = json.load(f)
            for task_type, tasks in osworld_data.items():
                self._osworld_tasks[task_type] = tasks
                print(f"Loaded {len(tasks)} {task_type} tasks from OSWorld")
        except FileNotFoundError:
            print(f"Warning: OSWorld tasks file not found at {osworld_path}")
        except json.JSONDecodeError as e:
            print(f"Error parsing OSWorld tasks file: {e}")

    def _expand_experiments(self) -> Iterator[ExperimentSpec]:
        """Expand declarative experiment configs into individual experiment specs."""
        experiments_config = self.config.get("experiments", {})
        categories_config = self.config.get("categories", {})

        for group_config in experiments_config.values():
            topologies = self._ensure_list(group_config.get("topology", []))
            categories = self._ensure_list(group_config.get("categories", []))
            levels = self._ensure_list(group_config.get("levels", []))
            obs_types = self._ensure_list(group_config.get("observation_types", []))
            task_types = self._ensure_list(group_config.get("task_types", []))

            for topology in topologies:
                for category in categories:
                    cat_config = categories_config.get(category, {})
                    is_osworld = cat_config.get("source_type") == "osworld" or category == "osworld"

                    if is_osworld:
                        for task_type in task_types or cat_config.get("task_types", []):
                            for obs_type in obs_types:
                                yield ExperimentSpec(
                                    name=self._build_experiment_name(topology, category, obs_type, task_type=task_type),
                                    topology=topology,
                                    category=category,
                                    observation_type=obs_type,
                                    task_type=task_type,
                                )
                    else:
                        available_levels = cat_config.get("levels", {})
                        for level in levels:
                            if level not in available_levels:
                                continue
                            for obs_type in obs_types:
                                yield ExperimentSpec(
                                    name=self._build_experiment_name(topology, category, obs_type, level=level),
                                    topology=topology,
                                    category=category,
                                    observation_type=obs_type,
                                    level=level,
                                )

    def _ensure_list(self, value) -> list:
        """Ensure value is a list."""
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def _build_experiment_name(self, topology: str, category: str, obs_type: str, level: int | None = None, task_type: str | None = None) -> str:
        """Build experiment name from components."""
        if topology == "single":
            prefix = "single_agent"
        else:
            prefix = f"multi_agent-{topology}"

        if task_type:
            return f"{prefix}-{task_type}_osworld-{obs_type}"

        return f"{prefix}-{category}-{obs_type}-l{level}"

    def _get_task_types_for_category(self, category: str, level: int | None) -> list[str]:
        """Get task types for a category and level."""
        categories_config = self.config.get("categories", {})
        cat_config = categories_config.get(category, {})

        if cat_config.get("source_type") == "osworld":
            return cat_config.get("task_types", [])

        if level and "levels" in cat_config:
            return cat_config["levels"].get(level, [])

        return []

    def _create_sampling_config(self, spec: ExperimentSpec) -> dict:
        """Create sampling configuration for an experiment spec."""
        defaults = self.config.get("defaults", {})
        instances_per_task = defaults.get("instances_per_task", 2)

        if spec.category == "osworld" or spec.task_type:
            return {
                "source_type": "osworld",
                "sampling": [
                    {
                        "category": "osworld",
                        "task_types": [spec.task_type] if spec.task_type else [],
                    }
                ],
                "output": {
                    "upload_s3": defaults.get("upload_s3", True),
                    "output_dir": defaults.get("output_dir", "outputs"),
                    "session_name": spec.name,
                },
            }

        task_types = self._get_task_types_for_category(spec.category, spec.level)
        return {
            "sampling": [
                {
                    "category": spec.category,
                    "levels": [spec.level] if spec.level else [],
                    "task_types": task_types,
                    "instances_per_task": instances_per_task,
                }
            ],
            "output": {
                "upload_s3": defaults.get("upload_s3", True),
                "output_dir": defaults.get("output_dir", "outputs"),
                "session_name": spec.name,
            },
        }

    def _create_output_directory(self, experiment_name: str) -> str:
        """Create and return the output directory path for an experiment."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiment_dir = Path("outputs") / "runs" / f"run_{timestamp}" / experiment_name
        experiment_dir.mkdir(parents=True, exist_ok=True)
        return str(experiment_dir)

    def _generate_tasks(self, config: dict, run_name: str):
        """Generate tasks using the TaskSamplingInterface."""
        from src.tasks.core.task_sampling import TaskSamplingInterface

        run_config = copy.deepcopy(config)
        run_config["output"]["output_dir"] = self._create_output_directory(run_name)

        sampler = TaskSamplingInterface()
        session = sampler.sample_from_dict(run_config)

        if session.failed_tasks:
            print(f"Warning: {len(session.failed_tasks)} tasks failed for '{run_name}'")
            for f in session.failed_tasks:
                print(f"  - {f.get('spec', '?')} -> {f.get('error', '?')}")

        return session

    def _generate_osworld_tasks(self, config: dict) -> OSWorldSession:
        """Generate OSWorld task information."""
        all_task_info = []
        for sample_spec in config.get("sampling", []):
            if sample_spec.get("category") != "osworld":
                continue
            for task_type in sample_spec.get("task_types", []):
                if task_type not in self._osworld_tasks:
                    print(f"Warning: OSWorld task type '{task_type}' not found")
                    continue
                for task in self._osworld_tasks[task_type]:
                    all_task_info.append(
                        {
                            "framework_config": task,
                            "category": "osworld",
                            "task_type": task_type,
                            "level": 1,
                            "instance": 1,
                        }
                    )
                print(f"Added {len(self._osworld_tasks[task_type])} {task_type} OSWorld tasks")
        return OSWorldSession(successful_tasks=all_task_info)

    def _extract_task_information(self, session) -> list:
        """Extract task information from a session."""
        if not session.successful_tasks:
            print("Warning: No successful tasks generated")
            return []

        task_info = []
        for pkg in session.successful_tasks:
            if isinstance(pkg, dict) and "framework_config" in pkg:
                task_info.append(pkg)
            else:
                task_info.append(
                    {
                        "framework_config": pkg.framework_config,
                        "category": pkg.spec.category,
                        "task_type": pkg.spec.task_type,
                        "level": pkg.spec.level,
                        "instance": pkg.spec.instance,
                    }
                )
        print(f"Extracted {len(task_info)} tasks")
        return task_info

    def _create_experiment_config(self, spec: ExperimentSpec) -> dict:
        """Create experiment configuration with system settings."""
        system = self.config["system"]
        # Prefer VM_PATH environment variable, then config.yaml, then constants default
        vm_path = os.getenv("VM_PATH") or system.get("vm_path")
        if not vm_path:
            from src.utils.constants import VM_PATH
            vm_path = VM_PATH
        return {
            "headless": system.get("headless", False),
            "observation_type": spec.observation_type,
            "action_space": "pyautogui",
            "screen_width": system["screen_width"],
            "screen_height": system["screen_height"],
            "path_to_vm": vm_path,
            "sleep_after_execution": 0,
            "max_retries": 2,
            "max_rounds": system["max_rounds"],
            "max_transitions_per_round": system["max_transitions_per_round"],
            "player_consecutive_violation_limit": system["player_consecutive_violation_limit"],
            "player_total_violation_limit": system["player_total_violation_limit"],
            "sliding_window_size": system.get("sliding_window_size"),
            "topology_type": spec.topology,
        }

    def on_generate(self, seed=None, **kwargs):
        """Generate all experiments from declarative configuration."""
        # Group experiments by their sampling config to enable task reuse
        sampling_to_specs: dict[str, list[ExperimentSpec]] = {}

        for spec in self._expand_experiments():
            if spec.task_type:
                cache_key = f"{spec.category}:{spec.task_type}"
            else:
                cache_key = f"{spec.category}:l{spec.level}"

            if cache_key not in sampling_to_specs:
                sampling_to_specs[cache_key] = []
            sampling_to_specs[cache_key].append(spec)

        # Generate tasks once per unique sampling config
        task_cache: dict[str, list] = {}

        print("INFO: Generating task sets...")
        for cache_key, specs in sampling_to_specs.items():
            if cache_key in task_cache:
                continue

            sample_config = self._create_sampling_config(specs[0])

            if sample_config.get("source_type") == "osworld":
                session = self._generate_osworld_tasks(sample_config)
            else:
                session = self._generate_tasks(sample_config, cache_key)

            task_cache[cache_key] = self._extract_task_information(session)
        print("INFO: Finished generating task sets.")

        # Create experiments
        print("INFO: Creating experiments...")
        for cache_key, specs in sampling_to_specs.items():
            task_info_list = task_cache[cache_key]

            for spec in specs:
                experiment = self.add_experiment(spec.name)
                experiment["environment_type"] = "osworld"
                experiment["templates"] = {"roles": [], "graph": {}}

                for idx, task_info in enumerate(task_info_list, start=1):
                    game_instance = self.add_game_instance(experiment, idx)
                    game_instance["task_config"] = task_info["framework_config"].copy()
                    game_instance["category"] = task_info["category"]
                    game_instance["task_type"] = task_info["task_type"]

                experiment["config"] = self._create_experiment_config(spec)

        print("INFO: Finished generating all experiments.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate computer game instances.")
    parser.add_argument("--test", action="store_true", help="Run test/demo code")
    args = parser.parse_args()

    generator = ColabGameInstanceGenerator("./")
    generator.generate(filename="instances.json")
    print("Generated instances file: instances.json")

    if args.test:
        print("\nGenerated experiments:")
        for spec in generator._expand_experiments():
            print(f"  - {spec.name}")
