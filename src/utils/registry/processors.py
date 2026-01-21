from typing import Callable, Dict, Any, List

from .base import Registry
from ..osworld import preprocess_observation


processors = Registry[Callable]()


@processors.register("observation")
def process_observation(observation: Dict[str, Any], game_config: Dict) -> Dict[str, Any]:
    """Process raw observation data into a standardized format
    Args:
        observation: Raw observation data
        game_config: Dictionary containing game configuration
    Returns:
        Processed observation data
    """
    # Check if screenshot exists and is a string (local path)
    if "screenshot" in observation and not isinstance(observation["screenshot"], str):
        raise ValueError(f"Expected 'screenshot' to be a string (local path), but got {type(observation['screenshot']).__name__}")

    processed_obs = {}
    observation_type = game_config.get("observation_type", "a11y_tree")
    platform = game_config.get("platform", "ubuntu")
    a11y_tree_max_tokens = game_config.get("a11y_tree_max_tokens", None)

    preprocessed_obs = preprocess_observation(
        observation=observation,
        observation_type=observation_type,
        platform=platform,
        a11y_tree_max_tokens=a11y_tree_max_tokens,
    )
    processed_obs.update(preprocessed_obs)

    return processed_obs


# Note: Removed pass-through processors (request, response, plan, task, additional)
# that simply returned their input unchanged. Callers should check if a processor
# exists before calling it, and use the raw value if no processor is registered.


@processors.register("blackboard")
def process_blackboard(blackboard: List[Dict], game_config: Dict) -> List[Dict]:
    """Process raw blackboard entries from BlackboardManager into standardized format
    Args:
        blackboard: List of blackboard entry dictionaries
        game_config: Dictionary containing game configuration
    Returns:
        Processed blackboard entries list
    """
    # Validate blackboard entries structure
    if not isinstance(blackboard, list):
        raise ValueError("Blackboard must be a list")

    # Ensure each entry has required fields
    for entry in blackboard:
        if not isinstance(entry, dict):
            raise ValueError("Blackboard entries must be dictionaries")
        if "role_id" not in entry or "content" not in entry:
            raise ValueError("Blackboard entries must have 'role_id' and 'content' fields")

    return blackboard
