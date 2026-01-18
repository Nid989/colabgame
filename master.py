import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
from PIL import Image

from clemcore import backends
from clemcore.clemgame import (
    GameMaster,
    GameSpec,
    GameBenchmark,
    GameScorer,
    ParseError,
    GameError,
    RuleViolationError,
)
from clemcore.clemgame import metrics
from src.master import NetworkDialogueGameMaster, EdgeCondition
from src.environment import Environment, EnvironmentFactory
from src.player import RoleBasedPlayer
from src.message import MessageType, MessageState, PlayerContextFormatter, CommunicationRuleTracker
from src.utils.registry.parsers import parsers, get_parser_metadata, process_content
from src.utils.image_manager import ImageManager
from src.topologies.factory import TopologyFactory
from src.topologies.base import TopologyType
from scorer import ComputerGameScorer

load_dotenv()

logger = logging.getLogger(__name__)


class ComputerGame(NetworkDialogueGameMaster):
    def __init__(
        self,
        game_spec: GameSpec,
        experiment: Dict,
        player_models: List[backends.Model],
    ):
        super().__init__(game_spec, experiment, player_models)
        self.env: Environment = None
        self.game_instance: Dict = None
        self.game_config: Dict = None
        self.message_state = MessageState()
        self.player_context_formatter = None
        self.aborted: bool = False
        self.fail: bool = False
        self.success: bool = False
        self.env_terminated: bool = False
        self.player_stats = {}
        self.round_stats = {}
        self._episode_score: float = 0.0
        self.communication_tracker = CommunicationRuleTracker()
        self._last_communication_partners = {}  # Track last communication partner for each player

        # Initialize communication rules configuration
        self.communication_rules_config = {
            "enable_rules": True,
            "exclude_topologies": ["single"],
            "cycle_threshold": 4,  # 4 messages = 2 round trips
            "violation_threshold": 3,  # 3 consecutive violations = game abort
        }

    def _on_setup(self, **game_instance) -> None:
        """Method executed at the start of the default setup method.

        Key Actions:
            - Prepares game configuration.
            - Sets up environment and loads initial observation + starts gameplay recording.
            - Constructs player interaction graph/network.
            - Sets up trigger pipeline (specific) 'parse func. -> after parse steps'

        Args:
            game_instance: Keyword arguments of the game_instance
        """
        self.game_instance = game_instance
        self.environment_type = self.experiment["environment_type"].lower()
        self._prepare_game_config()
        self._prepare_game_instance()
        self._initialize_formatter()
        self._initialize_environment()
        self._build_graph()
        self._initialize_topology_specific_components()
        self.topology_type = self.game_config.get("topology_type")

    def _prepare_game_config(self) -> None:
        """Prepare game configuration dictionary"""
        game_config = self.experiment["config"].copy()
        observation_type = game_config.get("observation_type", "a11y_tree")
        use_images = observation_type in ["screenshot", "screenshot_a11y_tree", "som"]
        require_a11y_tree = observation_type in [
            "a11y_tree",
            "screenshot_a11y_tree",
            "som",
        ]
        game_config.update({"use_images": use_images, "require_a11y_tree": require_a11y_tree})

        # Always initialize ImageManager for archival purposes
        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        aws_region = os.getenv("AWS_REGION")
        s3_bucket = os.getenv("S3_BUCKET_NAME")

        if not all([aws_access_key_id, aws_secret_access_key, aws_region, s3_bucket]):
            raise ValueError("Missing required S3 environment variables.")

        game_config["image_manager"] = ImageManager(
            game_id=self.game_instance["game_id"],
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_region=aws_region,
            s3_bucket=s3_bucket,
        )
        self.log_key("image_manager_s3_prefix", game_config["image_manager"].s3_prefix)

        game_config["topology_type"] = TopologyType(game_config["topology_type"])
        self.game_config = game_config

    def _prepare_game_instance(self) -> None:
        """Prepare the game instance by generating graph and roles from topology configuration."""
        graph, roles = self._generate_dynamic_graph_and_roles()
        self.game_instance["roles"] = roles
        self.game_instance["graph"] = graph

    def _generate_dynamic_graph_and_roles(self) -> Tuple[Dict, List[Dict]]:
        """Generate graph structure and roles based on topology configuration.

        Returns:
            Tuple containing the graph configuration and role definitions.
        """
        topology = TopologyFactory.create_topology(self.game_config["topology_type"])
        topology.load_game_instance_config(self.game_instance)

        topology_participants = topology.get_default_participants()
        self.game_instance["participants"] = topology_participants

        graph_config = topology.generate_graph(topology_participants)
        updated_roles = self._update_roles_for_topology(topology)

        return graph_config, updated_roles

    def _update_roles_for_topology(self, topology) -> List[Dict]:
        """Create fresh roles based on dynamic topology configuration.

        Args:
            topology: Topology instance with loaded configuration

        Returns:
            List[Dict]: Fresh role configurations created from topology
        """
        # Get the node assignments from the generated graph
        graph_config = self.game_instance.get("graph", {})
        node_assignments = graph_config.get("node_assignments", {})

        if not node_assignments:
            logger.warning("No node assignments found in graph config, falling back to basic role creation")
            return self._create_basic_roles_from_topology(topology)

        # Get topology configuration
        role_definitions = topology.topology_config.get("role_definitions", {}) if topology.topology_config else {}

        # Create fresh roles from topology configuration
        fresh_roles = []

        for topology_role_name, role_nodes in node_assignments.items():
            if topology_role_name in role_definitions:
                role_def = role_definitions[topology_role_name]

                # Create role configuration based on topology definition
                role_config = {
                    "name": topology_role_name,
                    "handler_type": role_def.get("handler_type", "standard"),
                    "allowed_components": role_def.get("allowed_components", []),
                    "receives_goal": role_def.get("receives_goal", False),
                    "message_permissions": role_def.get("message_permissions", {"send": [], "receive": []}),
                    # Add domain information for this role
                    "domains": [node["domain"] for node in role_nodes],
                    "node_count": len(role_nodes),
                }

                fresh_roles.append(role_config)

        logger.info(f"Created {len(fresh_roles)} fresh roles from topology configuration")
        return fresh_roles

    def _create_basic_roles_from_topology(self, topology) -> List[Dict]:
        """Fallback method to create basic roles when node assignments are not available."""
        role_definitions = topology.topology_config.get("role_definitions", {}) if topology.topology_config else {}

        basic_roles = []
        for role_name, role_def in role_definitions.items():
            role_config = {
                "name": role_name,
                "handler_type": role_def.get("handler_type", "standard"),
                "allowed_components": role_def.get("allowed_components", []),
                "receives_goal": role_def.get("receives_goal", False),
                "message_permissions": role_def.get("message_permissions", {"send": [], "receive": []}),
                "domains": ["general"],
                "node_count": 1,
            }
            basic_roles.append(role_config)

        return basic_roles

    def _initialize_formatter(self) -> None:
        """Initialize the player context formatter with the current game configuration."""
        self.player_context_formatter = PlayerContextFormatter(game_config=self.game_config)

    def _process_screenshot(self, observation: Dict) -> None:
        """Process screenshot: save to S3 and update observation based on observation type."""
        if "screenshot" not in observation or not isinstance(observation["screenshot"], bytes):
            return

        image_manager = self.game_config.get("image_manager")
        if not image_manager:
            return

        image_manager.save_image(observation["screenshot"])
        observation_type = self.game_config.get("observation_type", "a11y_tree")

        if observation_type in ["screenshot", "screenshot_a11y_tree", "som"]:
            local_path = image_manager.get_latest_image_path()
            observation["screenshot"] = local_path if local_path else None
        elif observation_type == "a11y_tree":
            observation.pop("screenshot", None)

        self.log_to_self("screenshot", {"image": [image_manager.get_latest_image_wget_link()]})

    def _initialize_environment(self) -> None:
        """Initializes game environment with recording capabilities and retrieves the initial state observation.

        Raises:
            RuntimeError: If environment or recording initialization fails
        """
        try:
            self.env = EnvironmentFactory.create_environment(self.environment_type, **self.game_config)
            observation = self.env.reset(task_config=self.game_instance["task_config"])
            self._process_screenshot(observation)
            self.message_state.update(observation=observation)
            if not self.env.start_recording():
                raise RuntimeError("Failed to start environment recording")
        except Exception as e:
            self.aborted = True
            error_message = f"Environment initialization failed: {str(e)}" if "recording" not in str(e).lower() else f"Recording initialization failed: {str(e)}"
            raise RuntimeError(error_message) from e

    def _build_graph(self) -> None:
        """Build player-network graph from game instance configuration.

        Raises:
            RuntimeError: If graph building fails
        """
        try:
            from src.utils.template_manager import PromptTemplateManager

            template_manager = PromptTemplateManager()
            graph_config = self.game_instance.get("graph")
            roles = self.game_instance.get("roles", [])

            self._create_player_nodes(graph_config, roles, template_manager)
            self._create_graph_edges(graph_config)

            anchor_node = graph_config.get("anchor_node")
            if anchor_node:
                self.set_anchor_node(anchor_node)

            logger.info("Graph building complete")
        except Exception as e:
            raise RuntimeError(f"Failed to build interaction graph: {str(e)}") from e

    def _create_player_nodes(self, graph_config: Dict, roles: List[Dict], template_manager) -> None:
        """Create player nodes from graph configuration.

        Args:
            graph_config: Graph configuration dictionary
            roles: List of role definitions
            template_manager: Template manager for prompt generation
        """
        from src.message import RoleConfig

        for node in graph_config.get("nodes", []):
            node_id = node.get("id")
            node_type = node.get("type")
            if node_type != "PLAYER" or not node_id:
                continue

            role_index = node.get("role_index", 0)
            role_config = RoleConfig.from_dict(roles[role_index])

            if not role_config.initial_prompt:
                role_config.initial_prompt = self._generate_initial_prompt(role_config, node_id, graph_config, template_manager)

            player = RoleBasedPlayer(
                self.player_models[0],
                role=node_id,
                handler_type=role_config.handler_type,
                allowed_components=role_config.allowed_components,
                message_permissions=role_config.message_permissions,
            )

            self.add_player_to_graph(
                player=player,
                initial_prompt=role_config.initial_prompt,
                node_id=node_id,
            )

    def _generate_initial_prompt(self, role_config, node_id: str, graph_config: Dict, template_manager) -> str:
        """Generate initial prompt for a role.

        Args:
            role_config: Role configuration object
            node_id: Node identifier
            graph_config: Graph configuration dictionary
            template_manager: Template manager for prompt generation

        Returns:
            Generated prompt string
        """
        from src.topologies.base import TopologyType

        participants = self.game_instance.get("participants")
        goal = self.game_instance["task_config"]["instruction"] if role_config.receives_goal else None

        topology_type_enum = self.game_config.get("topology_type")
        if isinstance(topology_type_enum, str):
            topology_type_enum = TopologyType(topology_type_enum.upper())

        return template_manager.generate_prompt(
            role_config,
            self.game_config.get("observation_type"),
            participants,
            node_id,
            goal,
            topology_type_enum,
            graph_config,
            self.game_config.get("max_rounds"),
            self.game_config,
        )

    def _create_graph_edges(self, graph_config: Dict) -> None:
        """Create edges in the graph from configuration.

        Args:
            graph_config: Graph configuration dictionary
        """
        for edge in graph_config.get("edges", []):
            from_node = edge.get("from")
            to_node = edge.get("to")
            edge_type = edge.get("type")
            description = edge.get("description", "")

            if not from_node or not to_node or not edge_type:
                continue

            if edge_type == "STANDARD":
                self.add_standard_edge(from_node, to_node, description)
            elif edge_type == "DECISION":
                condition_config = edge.get("condition", {})
                message_type = condition_config.get("type")
                if message_type not in MessageType.__members__:
                    raise KeyError(f"Invalid message-type field: {message_type}")
                condition = EdgeCondition(message_type=message_type, description=description)
                self.add_decision_edge(from_node, to_node, condition, description)

    def _initialize_topology_specific_components(self) -> None:
        """Initialize topology-specific components by delegating to topology classes."""
        topology_type = self.game_config["topology_type"]
        topology = TopologyFactory.create_topology(topology_type)
        components = topology.initialize_game_components(self.game_instance, self.game_config)

        for component_name, component_value in components.items():
            setattr(self, component_name, component_value)

        if not hasattr(self, "blackboard_manager"):
            self.blackboard_manager = None
        elif self.blackboard_manager:
            self._get_blackboard_context()

    def _get_next_node(self) -> str:
        """Get next node in round-robin sequence based on current node.

        Returns:
            Next node ID in the sequence
        """
        if not hasattr(self, "node_sequence") or not self.node_sequence:
            return None

        # Find current node's position in the sequence
        try:
            current_index = self.node_sequence.index(self._current_node)
        except ValueError:
            # Current node not in sequence, fallback to first node
            logger.warning(f"Current node {self._current_node} not found in node_sequence, using first node")
            return self.node_sequence[0] if self.node_sequence else None

        # Calculate next node index (round-robin)
        next_index = (current_index + 1) % len(self.node_sequence)
        next_node = self.node_sequence[next_index]

        return next_node

    def _get_blackboard_context(self) -> None:
        """Get blackboard context and store raw data in message state."""
        if self.blackboard_manager:
            # Get raw blackboard entries (not formatted)
            raw_entries = self.blackboard_manager.get_history()

            # Store raw entries in message state
            self.message_state.update(blackboard=raw_entries)

    def _does_game_proceed(self) -> bool:
        """Determine if the game should continue to the next turn.

        Returns:
            bool: False if game is completed or aborted, True otherwise.
        """
        # Stop if a critical error has occurred.
        if self.aborted:
            return False

        # Stop if the environment has signaled the game is over.
        if self.env_terminated:
            self.log_to_self("info", "Environment signaled termination.")
            return False

        # Stop if the game has reached the maximum number of rounds.
        max_rounds = self.game_config.get("max_rounds", 1)
        if self.current_round >= max_rounds:
            self.aborted = True
            reason = f"Maximum rounds {max_rounds} reached"
            self.log_to_self("aborted", {"reason": reason})
            return False

        # Stop if the game has reached the maximum number of transitions per round.
        # We check the number of transitions in the current round.
        max_transitions = self.game_config.get("max_transitions_per_round", 10)
        if self.transition.total_transitions >= max_transitions:
            self.aborted = True
            reason = f"Maximum transitions per round {max_transitions} reached"
            self.log_to_self("aborted", {"reason": reason})
            return False

        # Stop if the current node is the designated end node.
        if self._current_node == "END":
            self.log_to_self("info", "Reached END node.")
            return False

        return True

    def _set_context_for(self, player: RoleBasedPlayer, formatted_context: Dict) -> None:
        """Sets context for a player based on formatted context data.

        Args:
            player: Player instance to set context for
            formatted_context: Dictionary containing content and optional image data
        """
        if "image" in formatted_context and formatted_context["image"]:
            self.set_context_for(
                player,
                formatted_context["content"],
                image=formatted_context["image"],
            )
        else:
            self.set_context_for(player, formatted_context["content"])

    def _on_before_game(self):
        """Executed once at the start, before entering the play loop.

        Key Actions:
            - Adds the initial game-context to the anchor player
        """
        super()._on_before_game()
        assert self._current_node == self.anchor_node, "Current node must be the anchor node at game start"

        # Add round information to message state before creating context
        max_rounds = self.game_config.get("max_rounds", 1)
        if max_rounds > 1:
            self.message_state.update(round_info={"current_round": self.current_round, "max_rounds": max_rounds})

        context = self.player_context_formatter.create_context_for(self.message_state, self._current_player)
        self.message_state.reset(preserve=["observation", "blackboard"])  # NOTE: do we actually need to preserve blackboard?
        if context is None:
            logger.debug("No context generated for player; skipping inital context setup.")
            return
        self._set_context_for(self._current_player, context)
        logger.info(f"Set initial context for player at node {self._current_node}")

    def extract_json_codeblock(self, text: str) -> Tuple[bool, Optional[Dict[Any, Any]] | Exception]:
        """
        Extracts and parses JSON content from a string containing code blocks, only allowing no language identifier or 'json'.
        Args:
            text: Input string containing JSON within triple-backtick code blocks.
        Returns:
            Tuple[bool, Optional[Dict[Any, Any]] | Exception]: (success, result or error)
        """
        try:
            pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
            match = re.search(pattern, text)

            if not match:
                invalid_pattern = r"```[a-zA-Z]+\s*([\s\S]*?)\s*```"
                if re.search(invalid_pattern, text):
                    return False, ParseError(
                        reason="Code block found with invalid language identifier (only 'json' or no identifier allowed)",
                        response=text,
                    )
                return False, ParseError(reason="No code block found in the input text", response=text)

            json_content = match.group(1).strip()

            if not json_content:
                return False, ParseError(reason="Empty code block found", response=text)

            result = json.loads(json_content)

            if not isinstance(result, dict):
                return False, ParseError(reason="Parsed content is not a JSON object", response=json_content)

            logger.info("Successfully parsed JSON from code block")
            return True, result

        except json.JSONDecodeError as e:
            return False, ParseError(reason=f"Invalid JSON format: {str(e)}", response=text)

    def check_json_message(self, data: Dict[Any, Any]) -> Tuple[bool, Optional[MessageType] | Exception]:
        """
        Validates the JSON message structure and returns the MessageType.
        Args:
            data: Parsed JSON dictionary to validate.
        Returns:
            Tuple[bool, Optional[MessageType] | Exception]: (success, result or error)
        """
        try:
            required_keys = {"type", "from", "content"}
            missing_keys = required_keys - set(data.keys())
            if missing_keys:
                return False, ParseError(reason=f"Missing required keys: {missing_keys}", response=str(data))

            try:
                message_type = MessageType[data["type"]]
            except KeyError:
                valid_types = ", ".join(mt.name for mt in MessageType)
                return False, ParseError(
                    reason=f"Invalid message type: {data['type']}. Must be one of {valid_types}",
                    response=str(data),
                )

            # Validate role-based permissions
            current_player = self._current_player
            if hasattr(current_player, "validate_outgoing_message"):
                is_valid, error_msg = current_player.validate_outgoing_message(message_type)
                if not is_valid:
                    return False, ParseError(reason=error_msg, response=str(data))

            if message_type in MessageType.requires_to():
                if "to" not in data:
                    return False, ParseError(
                        reason=f"'to' field is required for {message_type.name} messages",
                        response=str(data),
                    )
                if not isinstance(data["to"], str):
                    return False, ParseError(
                        reason="Invalid type for 'to' field: must be a string",
                        response=str(data),
                    )

                # Validate target role can receive this message type
                target_role = data["to"]
                target_player = self.get_player_by_role(target_role)
                if target_player and hasattr(target_player, "validate_incoming_message"):
                    is_valid, error_msg = target_player.validate_incoming_message(message_type)
                    if not is_valid:
                        return False, ParseError(reason=error_msg, response=str(data))

            elif message_type in MessageType.prohibits_to():
                if "to" in data:
                    return False, ParseError(
                        reason=f"'to' field must not be present for {message_type.name} messages",
                        response=str(data),
                    )

            if not isinstance(data["from"], str):
                return False, ParseError(
                    reason="Invalid type for 'from' field: must be a string",
                    response=str(data),
                )

            # Validate 'from' field matches current player role
            if hasattr(current_player, "role") and data["from"] != current_player.role:
                return False, ParseError(
                    reason=f"'from' field must match current player role. Expected '{current_player.role}', got '{data['from']}'",
                    response=str(data),
                )

            # Basic content type validation
            if message_type == MessageType.EXECUTE and self.game_config.get("action_space") == "computer13":
                if not isinstance(data["content"], list) or not all(isinstance(item, dict) for item in data["content"]):
                    return False, ParseError(
                        reason="Invalid 'content' field for computer13: must be a list of dictionaries",
                        response=str(data),
                    )
            elif message_type in {
                MessageType.EXECUTE,
                MessageType.REQUEST,
                MessageType.RESPONSE,
                MessageType.STATUS,
                MessageType.TASK,
                MessageType.WRITE_BOARD,
            }:
                if not isinstance(data["content"], str):
                    return False, ParseError(
                        reason=f"Invalid 'content' field for {message_type.name}: must be a string",
                        response=str(data),
                    )

            logger.info("JSON message validated successfully")

            return True, message_type

        except Exception as e:
            logger.error(f"Validation failed: {str(e)}")
            return False, ParseError(
                reason=f"Unexpected error during validation: {str(e)}",
                response=str(data),
            )

    def handle_json_content(
        self,
        data: Any,
        message_type: str | MessageType,
        environment_type: str,
        action_space: Optional[str],
    ) -> Tuple[bool, Optional[Any] | Exception]:
        """
        Processes the content field based on message type, environment type, and action space.
        Args:
            data: The content to process (str for pyautogui/REQUEST/RESPONSE/STATUS/TASK, List[Dict] for computer13).
            message_type: The message type (string or MessageType enum, e.g., 'EXECUTE' or MessageType.EXECUTE).
            environment_type: The environment type (e.g., 'osworld').
            action_space: The action space (e.g., 'computer13') for EXECUTE messages.
        Returns:
            Tuple[bool, Optional[Any] | Exception]: (success, result or error)
        """
        try:
            # Convert message_type to string for registry
            message_type_str = message_type.name if isinstance(message_type, MessageType) else message_type

            # Call registry to process content
            success, result = process_content(data, message_type_str, environment_type, action_space)

            if not success:
                if isinstance(result, GameError):
                    return False, result  # Propagate GameError from parsers
                return False, ParseError(reason=str(result), response=str(data))

            if result is None:
                return False, ParseError(
                    reason=f"Failed to parse content for {message_type_str} in {environment_type} with action_space {action_space}",
                    response=str(data),
                )

            # Get target field from parser metadata
            parser_key = (message_type_str, environment_type, action_space)
            if parser_key not in parsers:
                parser_key = (message_type_str, environment_type, None)

            metadata = get_parser_metadata(parser_key)
            target_field = metadata.get("target_field")

            if target_field:
                # TODO: Update the game state with result
                # Example: setattr(game_state, target_field, result)
                logger.info(f"Processed content for {target_field}: {result}")
            else:
                logger.warning(f"No target field defined for parser {parser_key}")

            return True, result

        except Exception as e:
            logger.error(f"Unexpected error during content processing: {str(e)}")
            return False, ParseError(
                reason=f"Unexpected error during content processing: {str(e)}",
                response=str(data),
            )

    def _parse_response(self, player: RoleBasedPlayer, response: str) -> str:
        """Parse and validate player response, returning a JSON string.

        Args:
            player: Player object (part of class method signature).
            response: Input string containing JSON within code blocks.

        Returns:
            str: JSON string with validated fields (type, from, to, content).

        Raises:
            ParseError: If parsing or validation fails.
            GameError: If action content is invalid.
        """
        self.count_request()
        self._increment_request_stats()

        # Step 1: Extract JSON from code block
        success, json_data = self.extract_json_codeblock(response)
        if not success:
            raise json_data

        # Step 2: Validate message structure and get message type
        success, message_type = self.check_json_message(json_data)
        if not success:
            raise message_type

        # Step 3: Process content based on message type
        success, processed_content = self.handle_json_content(
            json_data["content"],
            message_type,
            self.environment_type,
            self.game_config["action_space"],
        )
        if not success:
            raise processed_content

        # Step 4: Construct final response
        parsed_response = self._construct_parsed_response(processed_content, message_type, json_data)

        self._increment_parsed_stats()
        return parsed_response

    def _increment_request_stats(self) -> None:
        """Increment request statistics for current player and round."""
        player_id = str(self._current_player.name)
        current_round = self.current_round

        self._ensure_player_stats_initialized(player_id, current_round)

        self.player_stats[player_id]["requests"] += 1
        self.round_stats[current_round]["requests"] += 1
        self.round_stats[current_round]["players"][player_id]["requests"] += 1

    def _increment_parsed_stats(self) -> None:
        """Increment parsed statistics for current player and round."""
        player_id = str(self._current_player.name)
        current_round = self.current_round

        self.player_stats[player_id]["parsed"] += 1
        self.round_stats[current_round]["parsed"] += 1
        self.round_stats[current_round]["players"][player_id]["parsed"] += 1

    def _construct_parsed_response(self, content: Any, message_type: MessageType, data: Dict) -> str:
        """Construct the final JSON response string.

        Args:
            content: The processed content.
            message_type: The validated MessageType.
            data: The original parsed JSON dictionary.

        Returns:
            str: JSON string of the parsed response.
        """
        parsed_response_dict = {
            "type": message_type.name,
            "from": data.get("from", ""),
        }
        if message_type in MessageType.requires_to() and "to" in data:
            parsed_response_dict["to"] = data["to"]
        parsed_response_dict["content"] = content
        return json.dumps(parsed_response_dict)

    def _execute_actions(self, actions: List[Union[str, Dict]]) -> Dict[str, Union[str, Image.Image, Dict]]:
        """Execute either pyautogui or computer13 actions and record observations.
        Args:
            actions: List of actions (pyautogui code strings or computer13 action dictionaries)
        Returns:
            Dict: Observation dictionary from the environment after executing actions
        Raises:
            GameError: If action execution fails or no observation is recorded
        """
        if not actions:
            raise GameError(reason="No actions to execute")

        observation = None
        for action in actions:
            try:
                # Assume self.env.step returns (observation, reward, done, info)
                observation, reward, done, info = self.env.step(action, self.game_config.get("sleep_after_execution", 0.0))
                if observation is None:
                    raise GameError(reason="Received None observation after action execution")

                self._process_screenshot(observation)

                if done:
                    self.env_terminated = True
                    logger.info("Game termination signal received (done=True)")
                    break
            except Exception as e:
                raise GameError(reason=f"Failed to execute action {str(action)}: {str(e)}")

        if observation is None:
            raise GameError(reason="No observation recorded after executing actions")
        return observation

    def _should_apply_communication_rules(self) -> bool:
        """Check if communication rules should be applied for current game state.

        Returns:
            bool: True if communication rules should be applied, False otherwise.
        """
        if not self.communication_rules_config.get("enable_rules", True):
            return False

        current_topology = str(self.game_config.get("topology_type", "")).lower()
        excluded_topologies = self.communication_rules_config.get("exclude_topologies", ["single"])
        return current_topology not in [t.lower() for t in excluded_topologies]

    def _validate_communication_rules(self, data: Dict, message_type: MessageType, player: RoleBasedPlayer) -> None:
        """Validate cycle-breaking communication rules for REQUEST and RESPONSE messages.

        Args:
            data: Parsed JSON response data
            message_type: Type of message being sent
            player: Current player sending the message

        Raises:
            RuleViolationError: If communication rules are violated
        """
        if not self._should_apply_communication_rules():
            return

        if message_type not in [MessageType.REQUEST, MessageType.RESPONSE]:
            return

        # Get target player from 'to' field
        recipient_node = data.get("to")
        recipient = self.get_player_from_node(recipient_node)
        recipient_name = str(recipient.name)
        if not recipient_name:
            return  # No target specified, skip validation

        sender_name = str(player.name)

        # Check if player is blocked from REQUEST/RESPONSE to this specific target
        if self.communication_tracker.is_communication_blocked(sender_name, recipient_name):
            # Player is blocked but trying to send REQUEST/RESPONSE to blocked target
            violation_count = self.communication_tracker.increment_violation_count(sender_name)
            violation_threshold = self.communication_rules_config.get("violation_threshold", 3)

            if violation_count >= violation_threshold:
                self.aborted = True
                reason = f"Player {sender_name} exceeded communication rule violation threshold ({violation_count}/{violation_threshold})"
                self.log_to_self("aborted", {"reason": reason})

            raise RuleViolationError(f"Cycle-breaking rule violation: Player {sender_name} is blocked from REQUEST/RESPONSE messages to {recipient_node} (must EXECUTE first)")

    def _update_communication_rules(self, data: Dict, message_type: MessageType, player: RoleBasedPlayer) -> None:
        """Update cycle-breaking communication rules state after successful message processing.

        Args:
            data: Processed JSON response data
            message_type: Type of message that was processed
            player: Player who sent the message
        """
        if not self._should_apply_communication_rules():
            return

        sender_name = str(player.name)

        # Handle EXECUTE messages - unblock the player completely and reset their cycles
        if message_type == MessageType.EXECUTE:
            self.communication_tracker.unblock_player_completely(sender_name)
            # Reset violation count for successful EXECUTE
            if sender_name in self.communication_tracker.rule_violations:
                self.communication_tracker.rule_violations[sender_name] = 0
            # Reset cycle counts for this player to allow fresh communication
            self.communication_tracker.reset_all_for_player(sender_name)
            return

        # Handle REQUEST and RESPONSE messages
        if message_type in [MessageType.REQUEST, MessageType.RESPONSE]:
            recipient_node = data.get("to")
            recipient = self.get_player_from_node(recipient_node)
            recipient_name = str(recipient.name)
            if not recipient_name:
                return  # No target specified, skip update

            # Check if player switched communication partner (only if they're talking to a completely different player)
            last_partner = self._last_communication_partners.get(sender_name)
            if last_partner and last_partner != recipient_name:
                # Player switched to a completely different partner - reset all cycles for the sender
                self.communication_tracker.reset_all_for_player(sender_name)

            # Update last communication partner
            self._last_communication_partners[sender_name] = recipient_name

            # Increment cycle count for this communication pair
            self.communication_tracker.increment_cycle_count(sender_name, recipient_name)

            # Check if cycle threshold is reached
            cycle_threshold = self.communication_rules_config.get("cycle_threshold", 4)
            current_cycle_count = self.communication_tracker.get_cycle_count(sender_name, recipient_name)

            if current_cycle_count >= cycle_threshold:
                # Cycle threshold reached - block players with EXECUTE privilege from this communication pair
                players_in_cycle = [player, recipient]

                for player_to_check in players_in_cycle:
                    if player_to_check.message_permissions:
                        player_permissions = player_to_check.message_permissions
                        if player_permissions and player_permissions.can_send(MessageType.EXECUTE):
                            # This player has EXECUTE privilege - block them from communicating with their cycle partner
                            other_player = recipient_name if player_to_check.name == sender_name else sender_name
                            self.communication_tracker.block_communication(player_to_check.name, other_player)

    def _advance_game(self, player: RoleBasedPlayer, parsed_response: str):
        """Advance the game state based on the player's response.
        Processes the response to determine node transitions and handle messages.

        Args:
            player: The RoleBasedPlayer instance providing the response.
            parsed_response: JSON string containing the parsed message with keys like
                             'type', 'from', 'to', and 'content'.

        Raises:
            GameError: If the message type is unknown or action execution fails.
            RuleViolationError: If no valid transition is found or if the message type does not
                                match the edge condition.
        """
        # Step 1: Parse the JSON response
        data = json.loads(parsed_response)
        message_type = MessageType[data["type"]]
        content = data["content"]

        # Step 1.5: Validate communication rules (NEW)
        self._validate_communication_rules(data, message_type, player)

        # Step 2: Apply topology-specific processing (NEW LAYER)
        processed_data = self._apply_topology_processing(data, message_type, player)

        # Step 3: Validate transition from current node
        current_node = self._current_node
        next_node = None
        from_role = player.role if hasattr(player, "role") else None
        to_role = processed_data.get("to") if "to" in processed_data else None

        if message_type == MessageType.STATUS:
            next_node = "END"
        else:
            # First, check decision edges for a valid transition
            decision_edges = self._get_decision_edges(current_node)
            if decision_edges:
                if "to" in processed_data:
                    target_node = processed_data["to"]
                    for to_node, condition in decision_edges:
                        if to_node == target_node and condition.validate(message_type.name, from_role, to_role):
                            next_node = target_node
                            break
                    if next_node is None:
                        raise RuleViolationError(f"No valid transition found to target node {target_node} with message type {message_type.name} from role {from_role}")
                else:
                    # Check for self-loop or staying at current node
                    for to_node, condition in decision_edges:
                        if to_node == current_node and condition.validate(message_type.name, from_role, to_role):
                            next_node = current_node
                            break
                    if next_node is None:
                        raise RuleViolationError(f"No valid self-loop transition found for message type {message_type.name} from role {from_role} at node {current_node}")

            # If no decision edge is found, fallback to standard edges
            if next_node is None:
                standard_edges = self._get_standard_edges(current_node)
                if standard_edges:
                    next_node = standard_edges[0][0]  # Take the first standard edge target node
                else:
                    raise RuleViolationError(f"No valid transition (decision or standard) found for message type {message_type.name} from role {from_role} from node {current_node}")

        # Step 4: Update game state with the validated transition
        self._update_round_tracking(current_node, next_node)
        self.transition.next_node = next_node
        logger.info(f"Transitioned from {current_node} to {next_node} based on message type {message_type.name}")

        # Step 5: Process message content based on type
        if message_type == MessageType.EXECUTE:
            observation = self._execute_actions(content)
            self.message_state.update(observation=observation)
        elif message_type == MessageType.STATUS:
            # Content is a list with one string, e.g., ["DONE"] or ["FAIL"]
            # TODO: Investigate if observation should be recorded after STATUS execution
            _ = self._execute_actions(content)
        elif message_type == MessageType.REQUEST:
            self.message_state.update(request=content)
        elif message_type == MessageType.RESPONSE:
            self.message_state.update(response=content)
        elif message_type == MessageType.WRITE_BOARD:
            self._write_to_blackboard(player, content)
        else:
            raise GameError(reason=f"Unknown message type: {message_type}")

        # Step 6: Prepare context for the next player if transition occurred
        if self.transition.next_node:
            next_player = self.get_player_from_node(self.transition.next_node)
            if next_player:
                # Get blackboard context and store in message state
                self._get_blackboard_context()

                # Add round information to message state before creating context
                max_rounds = self.game_config.get("max_rounds", 1)
                if max_rounds > 1:
                    self.message_state.update(round_info={"current_round": self.current_round + 1, "max_rounds": max_rounds})

                formatted_context = self.player_context_formatter.create_context_for(self.message_state, next_player)
                self._set_context_for(next_player, formatted_context)
                self.message_state.reset(preserve=["observation", "blackboard"])

        # A successful turn resets the consecutive violation counter for the current player
        player_id = str(self._current_player.name)
        if player_id in self.player_stats:
            self.player_stats[player_id]["violated_streak"] = 0

        # Reset round-level consecutive violations as well
        current_round = self.current_round
        if current_round in self.round_stats and player_id in self.round_stats[current_round]["players"]:
            self.round_stats[current_round]["players"][player_id]["violated_streak"] = 0

        # Update communication rules state after successful processing
        self._update_communication_rules(processed_data, message_type, player)

    def _apply_topology_processing(self, data: Dict, message_type: MessageType, player: RoleBasedPlayer) -> Dict:
        """
        Apply topology-specific processing to determine next node/agent.

        This method delegates to topology-specific processors implemented in topology classes.
        Each topology can implement its own logic for determining transitions.

        Args:
            data: Parsed JSON response data
            message_type: Type of message being processed
            player: Current player instance

        Returns:
            Dict: Modified data with topology-specific changes (e.g., added 'to' field)
        """
        topology_type = self.game_config.get("topology_type")

        # Create topology instance and delegate processing
        topology = TopologyFactory.create_topology(topology_type)

        # Prepare game context for topology processing
        game_context = {
            "current_node": self._current_node,
            "next_node_function": self._get_next_node,
            "node_sequence": getattr(self, "node_sequence", []),
        }

        return topology.process_message(data, message_type, player, game_context)

    def _write_to_blackboard(self, player: RoleBasedPlayer, content: str) -> None:
        """Write to the blackboard (WRITE_BOARD message).

        Args:
            player: The player writing to the blackboard
            content: Content to write to the blackboard
        """
        if self.blackboard_manager:
            role_id = player.role
            self.blackboard_manager.write_content(role_id, content)
            self.log_to_self("blackboard_write", {"role_id": role_id, "content": content, "entry_count": self.blackboard_manager.get_entry_count()})

    def _handle_player_violation(self) -> None:
        """Handle player violations: increment counts and check abortion limits."""
        self.count_request_violation()
        self._increment_violation_stats()
        self._check_violation_limits()

    def _increment_violation_stats(self) -> None:
        """Increment violation statistics for current player and round."""
        player_id = str(self._current_player.name)
        current_round = self.current_round

        self._ensure_player_stats_initialized(player_id, current_round)

        self.player_stats[player_id]["violated"] += 1
        self.player_stats[player_id]["violated_streak"] += 1
        self.round_stats[current_round]["violated"] += 1
        self.round_stats[current_round]["players"][player_id]["violated"] += 1
        self.round_stats[current_round]["players"][player_id]["violated_streak"] += 1

    def _ensure_player_stats_initialized(self, player_id: str, current_round: int) -> None:
        """Ensure player stats structures are initialized.

        Args:
            player_id: Player identifier
            current_round: Current round number
        """
        default_player_stats = {"requests": 0, "parsed": 0, "violated": 0, "violated_streak": 0}
        default_round_stats = {"requests": 0, "parsed": 0, "violated": 0, "players": {}}

        self.player_stats.setdefault(player_id, default_player_stats.copy())
        self.round_stats.setdefault(current_round, default_round_stats.copy())
        self.round_stats[current_round]["players"].setdefault(player_id, default_player_stats.copy())

    def _check_violation_limits(self) -> None:
        """Check if player has exceeded violation limits and abort if necessary."""
        player_id = str(self._current_player.name)
        consecutive_limit = self.game_config.get("player_consecutive_violation_limit", 3)
        total_limit = self.game_config.get("player_total_violation_limit", 5)

        consecutive_violations = self.player_stats[player_id]["violated_streak"]
        total_violations = self.player_stats[player_id]["violated"]

        if consecutive_violations >= consecutive_limit:
            self.aborted = True
            reason = f"Player {player_id} exceeded consecutive violation limit ({consecutive_violations}/{consecutive_limit})."
            self.log_to_self("aborted", {"reason": reason})
        elif total_violations >= total_limit:
            self.aborted = True
            reason = f"Player {player_id} exceeded total violation limit ({total_violations}/{total_limit})."
            self.log_to_self("aborted", {"reason": reason})

    def _on_parse_error(self, error: ParseError):
        """Hook to implement consequences for parsing errors e.g. prepare re-prompting or set game state to abort."""
        self.log_to_self("parse_error", str(error))

        # Convert technical error to user-friendly message
        user_friendly_error = self._convert_parse_error_to_user_message(error)
        self.message_state.update(error=user_friendly_error)
        formatted_context = self.player_context_formatter.create_context_for(self.message_state, self._current_player)
        self._set_context_for(self._current_player, formatted_context)
        self.message_state.reset(preserve=["observation", "blackboard"])

        self._handle_player_violation()

    def _convert_parse_error_to_user_message(self, error: ParseError) -> str:
        """Convert technical ParseError to user-friendly error message.

        Args:
            error: ParseError instance with technical details

        Returns:
            str: User-friendly error message
        """
        reason = error.reason.lower()

        # Pattern-to-message mapping for simple cases
        error_patterns = [
            ("no code block found", "Your response must be enclosed in code blocks (```)"),
            ("empty code block", "Your code block is empty"),
            ("invalid language identifier", "Your code block must not specify a language or use 'json' only"),
            ("invalid json format", "Your response contains invalid JSON format"),
            ("parsed content is not a json object", "Your response contains invalid JSON format"),
            ("invalid message type", "The message type you specified is not valid"),
            ("not allowed for your role", "The message type you specified is not allowed for your role"),
            ("validate_outgoing_message", "The message type you specified is not allowed for your role"),
            ("validate_incoming_message", "The target role cannot receive this message type"),
            ("'to' field is required", "Your message type requires specifying a recipient in the 'to' field"),
            ("'to' field must not be present", "Your message type should not include a 'to' field"),
            ("'from' field must match current player role", "The 'from' field must match your current role"),
            ("invalid python syntax", "Your Python code contains syntax errors"),
            ("pyautogui content must be a non-empty string", "Your PyAutoGUI content cannot be empty"),
        ]

        for pattern, message in error_patterns:
            if pattern in reason:
                return message

        # Handle missing required keys with specific field detection
        if "missing required keys" in reason:
            for field in ["type", "from", "content"]:
                if field in reason:
                    return f"Your response is missing the '{field}' field"
            return "Your response is missing required fields"

        # Handle content field errors
        if "invalid 'content' field" in reason:
            if "computer13" in reason:
                return "Your content must be a list of action dictionaries for computer13"
            return "Your content format is incorrect for this message type"

        return "Your response format is incorrect"

    def _on_game_error(self, error: GameError):
        """Hook to implement consequences for game errors e.g. prepare re-prompting or set game state to failure."""
        self.log_to_self("game_error", str(error))

        # Convert technical error to user-friendly message
        user_friendly_error = self._convert_game_error_to_user_message(error)
        self.message_state.update(error=user_friendly_error)
        formatted_context = self.player_context_formatter.create_context_for(self.message_state, self._current_player)
        self._set_context_for(self._current_player, formatted_context)
        self.message_state.reset(preserve=["observation", "blackboard"])

        self._handle_player_violation()

    def _convert_game_error_to_user_message(self, error: GameError) -> str:
        """Convert technical GameError to user-friendly error message.

        Args:
            error: GameError instance with technical details

        Returns:
            str: User-friendly error message
        """
        reason = error.reason.lower()

        # Pattern-to-message mapping
        error_patterns = [
            ("no actions to execute", "Your action list is empty"),
            ("received none observation after action execution", "The action could not be completed successfully"),
            ("failed to execute action", "The action you specified could not be executed"),
            ("no observation recorded after executing actions", "The action execution did not complete properly"),
            ("unknown message type", "The message type you specified is not recognized"),
            ("forbidden function", "Your code contains a function that is not allowed"),
            ("invalid context", "Your action is not valid in the current environment"),
        ]

        for pattern, message in error_patterns:
            if pattern in reason:
                return message

        # Special handling for cycle-breaking violations with dynamic target extraction
        if "cycle-breaking rule violation" in reason:
            target_match = re.search(r"to (\w+)", reason)
            target_player = target_match.group(1) if target_match else "this player"
            return f"You are blocked from sending REQUEST/RESPONSE messages to {target_player}. Use EXECUTE first to break the communication cycle."

        return "Your action could not be processed"

    def _on_after_game(self):
        """
        Called after the game ends (when _does_game_proceed returns False), before exiting the play loop.
        Evaluates the environment, sets success/fail state, and logs episode metrics.
        """
        # Step 1: Evaluate the episode and set success/fail/score flags
        # CHANGED: Episode score is now calculated independently of aborted state
        # Previously: if not self.aborted: self._episode_score = float(self.env.evaluate()) else: self._episode_score = 0.0
        # Now: Always evaluate environment to get true performance score
        self._episode_score = float(self.env.evaluate())
        self.success = self._episode_score == 1.0
        self.fail = not self.success

        # Step 2: Log all final summary data for the episode.
        # Use metrics.* constants to match scorer expectations
        image_manager_s3_prefix = None
        if "image_manager" in self.game_config:
            image_manager_s3_prefix = self.game_config["image_manager"].s3_prefix

        log_keys = [
            (metrics.METRIC_SUCCESS, 1 if self.success else 0),
            (metrics.METRIC_LOSE, 1 if self.fail else 0),
            (metrics.METRIC_ABORTED, 1 if self.aborted else 0),
            ("episode_score", self._episode_score),
            ("image_manager_s3_prefix", image_manager_s3_prefix),
            ("player_stats", self.player_stats),
            ("round_stats", self.round_stats),
        ]
        for key, value in log_keys:
            self.log_key(key, value)
            if key in ["player_stats", "round_stats"]:
                continue
            self.log_to_self(key, value)

        # Cleanup image manager resources
        if "image_manager" in self.game_config:
            self.game_config["image_manager"].cleanup()

    def compute_episode_score(self):
        """
        Returns the score for the current episode.
        The score is pre-computed and stored in _on_after_game.

        Returns:
            float: A score of 1.0 for success or 0.0 for failure.
        """
        return self._episode_score

    def get_player_by_role(self, role_identifier: str) -> Optional[RoleBasedPlayer]:
        """Get a player by their role identifier (e.g., 'executor_1', 'advisor', etc.).

        Args:
            role_identifier: The role identifier to search for

        Returns:
            RoleBasedPlayer or None if not found
        """
        # Search through all players using the inherited players_by_names
        for player in self.players_by_names.values():
            if hasattr(player, "role") and player.role == role_identifier:
                return player

        # If exact match not found, try to find by base role type
        # This handles cases where we're looking for 'executor' but have 'executor_1'
        for player in self.players_by_names.values():
            if hasattr(player, "role"):
                # Extract base role type (e.g., 'executor' from 'executor_1')
                base_role = player.role.split("_")[0] if "_" in player.role else player.role
                if base_role == role_identifier:
                    return player

        return None


class ComputerGameBenchmark(GameBenchmark):
    def __init__(self, game_spec: GameSpec):
        super().__init__(game_spec)

    def create_game_master(self, experiment: Dict, player_models: List[backends.Model]) -> GameMaster:
        return ComputerGame(self.game_spec, experiment, player_models)

    def create_game_scorer(self, experiment: Dict, game_instance: Dict) -> GameScorer:
        return ComputerGameScorer(self.game_name, experiment, game_instance)


if __name__ == "__main__":

    def load_json(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    experiments = load_json("./in/instances.json")
    experiment_1 = experiments["experiments"][0]
    game_1 = experiment_1["game_instances"][0]
    master = ComputerGame("computergame", None, experiment_1, ["mock", "mock"])
    master.setup(**game_1)
