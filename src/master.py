import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import networkx as nx

from clemcore import backends
from clemcore.clemgame import DialogueGameMaster

from .player import RoleBasedPlayer
from .topologies.base import TopologyType

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Enum representing different types of nodes in the network."""

    START = auto()
    PLAYER = auto()
    END = auto()


class EdgeType(Enum):
    """Enum representing different types of edges in the network."""

    STANDARD = auto()  # Direct connection, always traversed if no other decision edges are taken
    DECISION = auto()  # Conditional connection, traversed only if condition evaluates to True


class EdgeCondition:
    """Condition for transitioning between nodes based on MessageType and optional role permissions."""

    def __init__(
        self,
        message_type: str,
        description: str = "",
        allowed_from_roles: Optional[List[str]] = None,
        allowed_to_roles: Optional[List[str]] = None,
    ):
        self.message_type = message_type
        self.description = description
        self.allowed_from_roles = allowed_from_roles or []
        self.allowed_to_roles = allowed_to_roles or []

    def _role_matches(self, role: str, allowed_roles: List[str]) -> bool:
        """Check if role matches allowed roles (supports base role matching)."""
        if not allowed_roles or not role:
            return True
        base_role = role.split("_")[0] if "_" in role else role
        return role in allowed_roles or base_role in allowed_roles

    def validate(self, message_type: str, from_role: Optional[str] = None, to_role: Optional[str] = None) -> bool:
        """Validate if the provided MessageType and roles match this condition."""
        if self.message_type != message_type:
            return False
        if not self._role_matches(from_role, self.allowed_from_roles):
            return False
        if not self._role_matches(to_role, self.allowed_to_roles):
            return False
        return True

    def __str__(self) -> str:
        parts = [f"MessageType: {self.message_type}"]
        if self.allowed_from_roles:
            parts.append(f"From: {', '.join(self.allowed_from_roles)}")
        if self.allowed_to_roles:
            parts.append(f"To: {', '.join(self.allowed_to_roles)}")
        if self.description:
            parts.append(f"Description: {self.description}")
        return " | ".join(parts)


class NetworkDialogueGameMaster(DialogueGameMaster):
    """Extends DialogueGameMaster with a graph-based interaction model.

    Nodes represent game states or players, and edges represent transitions
    (standard and decision-based).
    """

    def __init__(
        self,
        game_spec,
        experiment: dict,
        player_models: List[backends.Model],
    ):
        super().__init__(game_spec, experiment, player_models)
        self.graph = nx.MultiDiGraph()
        self.graph.add_node("START", type=NodeType.START)
        self.graph.add_node("END", type=NodeType.END)
        self._current_node = "START"
        self.node_positions: Optional[Dict[str, Tuple[float, float]]] = None
        self.edge_labels: Dict[Tuple[str, str, str], str] = {}
        self.anchor_node: Optional[str] = None
        self.current_round_nodes: List[str] = []
        self.non_anchor_visited = False
        self.round_complete = False
        self.transition = NodeTransition()
        self.topology_type: Optional[TopologyType] = None

    @property
    def current_node(self) -> str:
        return self._current_node

    def add_player_to_graph(
        self,
        player: RoleBasedPlayer,
        initial_prompt: Optional[Union[str, Dict]] = None,
        initial_context: Optional[Union[str, Dict]] = None,
        node_id: Optional[str] = None,
    ) -> None:
        """Add a player to the game and the graph."""
        super().add_player(player, initial_prompt=initial_prompt, initial_context=initial_context)
        node_id = node_id or player.name
        self.graph.add_node(node_id, type=NodeType.PLAYER, player=player)

    def _validate_nodes_exist(self, *node_ids: str) -> None:
        """Validate that all specified nodes exist in the graph."""
        for node_id in node_ids:
            if node_id not in self.graph:
                raise ValueError(f"Node '{node_id}' does not exist in the graph")

    def add_standard_edge(self, from_node: str, to_node: str, label: str = "") -> None:
        """Add a standard edge between nodes (traversed when no decision edges are taken).

        Raises:
            ValueError: If nodes don't exist or a standard edge already exists.
        """
        self._validate_nodes_exist(from_node, to_node)

        has_existing = any(data.get("type") == EdgeType.STANDARD for _, v, data in self.graph.edges(from_node, data=True) if v == to_node)
        if has_existing:
            raise ValueError(f"A standard edge already exists from '{from_node}' to '{to_node}'")

        edge_key = f"standard_{from_node}_{to_node}"
        self.graph.add_edge(from_node, to_node, type=EdgeType.STANDARD, condition=None, key=edge_key)
        if label:
            self.edge_labels[(from_node, to_node, edge_key)] = label

    def add_decision_edge(self, from_node: str, to_node: str, condition: EdgeCondition, label: str = "") -> None:
        """Add a decision edge between nodes (traversed only if condition is satisfied).

        Raises:
            ValueError: If either node does not exist in the graph.
        """
        self._validate_nodes_exist(from_node, to_node)

        edge_count = sum(1 for _ in self.graph.edges(from_node, to_node, keys=True))
        edge_key = f"decision_{from_node}_{to_node}_{edge_count}"
        self.graph.add_edge(from_node, to_node, type=EdgeType.DECISION, condition=condition, key=edge_key)

        edge_label = label or (condition.description if condition else "")
        if edge_label:
            self.edge_labels[(from_node, to_node, edge_key)] = edge_label

    def set_anchor_node(self, node_id: str) -> None:
        """Set the anchor node for round tracking.

        Raises:
            ValueError: If the node does not exist in the graph.
        """
        self._validate_nodes_exist(node_id)
        self.anchor_node = node_id
        logger.info(f"Anchor node set to '{node_id}'")

    def _should_pass_turn(self) -> bool:
        """Check if transition to a different node is needed."""
        return self.transition.next_node is not None and self.transition.next_node != self._current_node

    def _next_player(self) -> RoleBasedPlayer:
        """Determine the next player based on graph transitions."""
        next_node = self.transition.next_node
        if not next_node or next_node not in self.graph:
            return self._current_player

        node_data = self.graph.nodes[next_node]
        self._current_node = next_node

        if node_data["type"] == NodeType.PLAYER:
            return node_data["player"]
        return self._current_player

    def _start_next_round(self) -> bool:
        """Check if a new round should start based on anchor node."""
        return self.round_complete

    def _on_after_round(self) -> None:
        """Handle post-round cleanup and preparation for the next round."""
        self._reset_round_tracking()
        self.log_next_round()

    def _on_before_game(self) -> None:
        """Handle setup before entering the main play loop.

        Raises:
            ValueError: If START node has no standard edges or anchor_node is invalid.
        """
        connected_nodes = [to_node for _, to_node, edge_data in self.graph.out_edges("START", data=True) if edge_data.get("type") == EdgeType.STANDARD]

        if not connected_nodes:
            raise ValueError("No standard edges found from START node")

        if self.anchor_node is not None:
            if self.anchor_node not in connected_nodes:
                raise ValueError(f"Anchor node '{self.anchor_node}' is not among the connected nodes from START: {connected_nodes}")
            self._current_node = self.anchor_node
        else:
            self._current_node = connected_nodes[0]

        self._current_player = self.get_player_from_node(self._current_node)
        self._update_round_tracking("START", self._current_node)

    def _update_round_tracking(self, prev_node: str, next_node: str) -> None:
        """Update round tracking state based on node transitions.

        A round completes when:
        - Transitioning to the END node
        - Single-agent topology: self-loop at anchor
        - Other topologies: returning to anchor after visiting other nodes
        """
        if self.anchor_node is None:
            return

        self.current_round_nodes.append(next_node)
        self.transition.total_transitions += 1

        if next_node != self.anchor_node:
            self.non_anchor_visited = True

        node_data = self.graph.nodes.get(next_node, {})
        is_end_node = node_data.get("type") == NodeType.END
        is_anchor_return = next_node == self.anchor_node

        if is_end_node:
            self.round_complete = True
        elif is_anchor_return:
            if self.topology_type == TopologyType.SINGLE or self.non_anchor_visited:
                self.round_complete = True

    def _reset_round_tracking(self) -> None:
        """Reset round tracking state and log completion."""
        round_path = " -> ".join(str(node) for node in self.current_round_nodes)
        self.log_to_self(
            "round-complete",
            f"Round completed: {round_path} (Total transitions: {self.transition.total_transitions})",
        )

        self.current_round_nodes = [self._current_node] if self._current_node else []
        self.non_anchor_visited = False
        self.round_complete = False
        self.transition.total_transitions = 0

    def get_player_from_node(self, node_id: str) -> Optional[RoleBasedPlayer]:
        """Get player associated with a node ID, or None if not a player node."""
        if node_id not in self.graph:
            return None
        node_data = self.graph.nodes[node_id]
        if node_data.get("type") == NodeType.PLAYER:
            return node_data.get("player")
        return None

    def get_node_from_player(self, player: RoleBasedPlayer) -> Optional[str]:
        """Get node ID associated with a player instance, or None if not found."""
        for node_id, node_data in self.graph.nodes(data=True):
            if node_data.get("type") == NodeType.PLAYER and node_data.get("player") == player:
                return node_id
        return None

    def _get_decision_edges(self, node_id: str) -> List[Tuple[str, EdgeCondition]]:
        """Retrieve all decision edges originating from a node.

        Raises:
            ValueError: If the node does not exist in the graph.
        """
        self._validate_nodes_exist(node_id)
        return [(to_node, edge_data["condition"]) for _, to_node, edge_data in self.graph.out_edges(node_id, data=True) if edge_data.get("type") == EdgeType.DECISION and edge_data.get("condition")]

    def _get_standard_edges(self, node_id: str) -> List[Tuple[str, Dict]]:
        """Retrieve all standard edges originating from a node.

        Raises:
            ValueError: If the node does not exist in the graph.
        """
        self._validate_nodes_exist(node_id)
        return [(to_node, edge_data) for _, to_node, edge_data in self.graph.out_edges(node_id, data=True) if edge_data.get("type") == EdgeType.STANDARD]

    def compute_turn_score(self) -> int:
        """Return turn score (required by DialogueGameMaster interface)."""
        return 0

    def visualize_graph(
        self,
        figsize: Tuple[int, int] = (12, 10),
        save_path: Optional[str] = None,
        dpi: int = 100,
    ) -> None:
        """Visualize the network structure with professional styling."""
        plt.figure(figsize=figsize, dpi=dpi)

        if not self.node_positions:
            self.node_positions = self._compute_node_positions()

        node_colors = {
            NodeType.START: "#4CAF50",
            NodeType.PLAYER: "#0288D1",
            NodeType.END: "#D81B60",
        }
        base_node_size = 3300

        self._draw_edges(base_node_size)
        self._draw_nodes(node_colors, base_node_size)
        self._draw_labels()
        self._draw_anchor_highlight(base_node_size)
        self._draw_legend(node_colors)

        plt.title(f"Interaction Network for {self.game_name}", fontsize=16, fontweight="bold", pad=20)
        plt.axis("off")
        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, bbox_inches="tight", dpi=dpi)
        else:
            plt.show()

    def _compute_node_positions(self) -> Dict[str, Tuple[float, float]]:
        """Compute node positions for visualization."""
        try:
            return nx.nx_pydot.pydot_layout(self.graph, prog="dot")
        except Exception:
            return nx.spring_layout(self.graph, k=0.5, iterations=100, seed=42)

    def _draw_edges(self, node_size: int) -> None:
        """Draw graph edges (standard and decision)."""
        edge_config = {"connectionstyle": "arc3,rad=0.1", "arrowstyle": "-|>", "node_size": node_size}

        standard_edges = [(u, v) for u, v, d in self.graph.edges(data=True) if d.get("type") == EdgeType.STANDARD]
        decision_edges = [(u, v) for u, v, d in self.graph.edges(data=True) if d.get("type") == EdgeType.DECISION]

        nx.draw_networkx_edges(self.graph, self.node_positions, edgelist=standard_edges, arrowsize=25, width=2.5, edge_color="#455A64", **edge_config)
        nx.draw_networkx_edges(self.graph, self.node_positions, edgelist=decision_edges, arrowsize=25, width=2, edge_color="#7B1FA2", style="dashed", **edge_config)

    def _draw_nodes(self, node_colors: Dict[NodeType, str], node_size: int) -> None:
        """Draw graph nodes by type."""
        for node_type in NodeType:
            nodes = [n for n in self.graph.nodes() if self.graph.nodes[n].get("type") == node_type]
            if nodes:
                nx.draw_networkx_nodes(self.graph, self.node_positions, nodelist=nodes, node_color=node_colors[node_type], node_size=node_size, alpha=0.9, edgecolors="#37474F", linewidths=2)

    def _draw_labels(self) -> None:
        """Draw node and edge labels."""
        node_labels = {}
        for node in self.graph.nodes():
            node_type = self.graph.nodes[node].get("type")
            if node_type == NodeType.PLAYER:
                player = self.graph.nodes[node].get("player")
                role_text = getattr(player, "role", None) or str(player.model)
                node_labels[node] = f"{node}\n({role_text})"
            else:
                node_labels[node] = node

        nx.draw_networkx_labels(self.graph, self.node_positions, labels=node_labels, font_size=10, font_family="sans-serif", font_weight="bold", font_color="#FFFFFF")

        edge_labels_dict = {(u, v): self._wrap_label(label, max_width=20) for (u, v, _), label in self.edge_labels.items() if label}
        nx.draw_networkx_edge_labels(self.graph, self.node_positions, edge_labels=edge_labels_dict, font_size=8, font_family="sans-serif", font_weight="normal", label_pos=0.4, bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.7, "boxstyle": "round,pad=0.4"})

    def _wrap_label(self, label: str, max_width: int) -> str:
        """Wrap label text to fit within max_width characters per line."""
        if len(label) <= max_width:
            return label
        words = label.split()
        lines, current_line = [], []
        current_length = 0
        for word in words:
            if current_length + len(word) > max_width and current_line:
                lines.append(" ".join(current_line))
                current_line, current_length = [word], len(word)
            else:
                current_line.append(word)
                current_length += len(word) + 1
        if current_line:
            lines.append(" ".join(current_line))
        return "\n".join(lines)

    def _draw_anchor_highlight(self, node_size: int) -> None:
        """Draw highlight ring around anchor node."""
        if self.anchor_node and self.anchor_node in self.graph:
            nx.draw_networkx_nodes(self.graph, {self.anchor_node: self.node_positions[self.anchor_node]}, nodelist=[self.anchor_node], node_color="none", node_size=int(node_size * 1.1), alpha=1.0, edgecolors="#FBC02D", linewidths=4)

    def _draw_legend(self, node_colors: Dict[NodeType, str]) -> None:
        """Draw the legend for the graph visualization."""
        legend_elements = [
            plt.Line2D([0], [0], color=node_colors[NodeType.START], marker="o", linestyle="None", markersize=15, label="Start Node"),
            plt.Line2D([0], [0], color=node_colors[NodeType.PLAYER], marker="o", linestyle="None", markersize=15, label="Player Node"),
            plt.Line2D([0], [0], color=node_colors[NodeType.END], marker="o", linestyle="None", markersize=15, label="End Node"),
            plt.Line2D([0], [0], color="#455A64", linewidth=2.5, label="Standard Edge"),
            plt.Line2D([0], [0], color="#7B1FA2", linewidth=2, linestyle="dashed", label="Decision Edge"),
        ]
        if self.anchor_node:
            legend_elements.append(plt.Line2D([0], [0], markerfacecolor="none", markeredgecolor="#FBC02D", marker="o", linestyle="None", markersize=15, markeredgewidth=2, label="Anchor Node"))

        plt.legend(handles=legend_elements, loc="lower center", bbox_to_anchor=(0.5, -0.15), ncol=3, fontsize=10, frameon=True, edgecolor="#37474F", facecolor="#FAFAFA", framealpha=0.9, bbox_transform=plt.gcf().transFigure, borderpad=1, labelspacing=1, handletextpad=1)

    def set_node_positions(self, positions: Dict[str, Tuple[float, float]]) -> None:
        """Set custom positions for nodes in the visualization."""
        self.node_positions = positions


@dataclass
class NodeTransition:
    """Temporary storage for node transition data.

    Attributes:
        next_node: ID of the next node to transition to, if any.
        total_transitions: Counter for total transitions in current round.
    """

    next_node: Optional[str] = None
    total_transitions: int = 0
