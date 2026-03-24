"""
sdg.py  —  State-Dependency Graph for VirtualHome

Parses virtualhome.pddl and builds a directed bipartite graph:
  - Action nodes  : each action in the PDDL domain
  - State nodes   : each predicate / property that appears in
                    a precondition or effect of any action

Edges:
  state  -> action  :  the state is a PRECONDITION of the action
  action -> state   :  the action CAUSES (adds or removes) the state

The graph is the authoritative source for:
  1. Checking whether a planned action's preconditions are met
  2. Tracing the root cause of a precondition failure back through
     the action history  (Error Backtrack & Diagnosis)
  3. Constraining the BFS search in Adaptive Action SubTree Generation

Terminology kept consistent with the SDA-Planner paper:
  Sdep[a]  — dependency (precondition) state set for action a
  Seff[a]  — effect state set for action a
  "state preparation action" — action whose node has exactly one
     outgoing edge to an agent-state node and no incoming edges from
     other state nodes  (e.g. WALK / FIND)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StateNode:
    """A predicate variable with an expected value (True = holds, False = negated)."""
    predicate: str          # e.g. "next_to", "holds_rh", "closed"
    value: bool = True      # True  → predicate must / does hold
                            # False → predicate must NOT / is removed
    agent_state: bool = False   # True if this is a character-level state
                                # (sitting, lying, holds_rh, holds_lh, next_to, inside)

    def __hash__(self):
        return hash((self.predicate, self.value))

    def __eq__(self, other):
        return (isinstance(other, StateNode) and
                self.predicate == other.predicate and
                self.value == other.value)

    def __repr__(self):
        prefix = "NOT " if not self.value else ""
        return f"State({prefix}{self.predicate})"


@dataclass
class ActionNode:
    """Represents one PDDL action and its dependency / effect sets."""
    name: str                               # e.g. "grab", "switch_on"
    sdep: List[StateNode] = field(default_factory=list)   # precondition states
    seff: List[StateNode] = field(default_factory=list)   # effect states
    is_state_prep: bool = False             # set after full graph is built

    def __repr__(self):
        return f"Action({self.name})"


class StateDependencyGraph:
    """
    Bipartite directed graph:
        state_node  ->  action_node   (precondition edge)
        action_node ->  state_node    (effect edge)

    Public API
    ----------
    sdep(action_name)           -> List[StateNode]
    seff(action_name)           -> List[StateNode]
    preconditions_satisfied(action_name, env_state) -> (bool, List[StateNode])
    is_state_prep_action(action_name)               -> bool
    actions_that_produce(predicate, value)          -> List[str]
    actions_that_require(predicate, value)          -> List[str]
    """

    # Predicates that relate to the CHARACTER (agent) rather than objects.
    # Used to classify StateNodes as agent_state=True.
    AGENT_PREDICATES: Set[str] = {
        "sitting", "lying",
        "next_to",          # char proximity
        "holds_rh", "holds_lh",
        "inside",           # char inside room
        "facing",
        "ontop",            # char on object (sit/lie result)
    }

    def __init__(self):
        self.actions: Dict[str, ActionNode] = {}
        self._state_to_actions: Dict[Tuple[str, bool], List[str]] = {}  # precond index
        self._action_to_states: Dict[str, List[StateNode]] = {}          # effect index

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    @classmethod
    def from_pddl(cls, pddl_path: str) -> "StateDependencyGraph":
        """Parse a PDDL domain file and return a populated SDG."""
        graph = cls()
        with open(pddl_path, "r") as f:
            content = f.read()
        graph._parse_pddl(content)
        graph._mark_state_prep_actions()
        return graph

    def _parse_pddl(self, content: str):
        """Extract all :action blocks and build action nodes."""
        # Strip comments
        content = re.sub(r";.*", "", content)
        # Find all action blocks
        action_blocks = re.findall(
            r"\(:action\s+([\w_]+)(.*?)(?=\(:action|\Z)",
            content, re.DOTALL
        )
        for action_name, body in action_blocks:
            action_name = action_name.strip().lower()
            precond_text = self._extract_section(body, ":precondition")
            effect_text  = self._extract_section(body, ":effect")

            sdep = self._parse_state_nodes(precond_text)
            seff = self._parse_state_nodes(effect_text)

            node = ActionNode(name=action_name, sdep=sdep, seff=seff)
            self.actions[action_name] = node

            # Index: state -> actions that require it
            for s in sdep:
                key = (s.predicate, s.value)
                self._state_to_actions.setdefault(key, []).append(action_name)

            # Index: action -> states it produces
            self._action_to_states[action_name] = seff

    def _extract_section(self, body: str, keyword: str) -> str:
        """Return the content of a PDDL section like :precondition or :effect."""
        idx = body.find(keyword)
        if idx == -1:
            return ""
        # Walk forward to find the matching paren block
        start = body.index("(", idx)
        depth = 0
        for i, ch in enumerate(body[start:], start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return body[start: i + 1]
        return body[start:]

    def _parse_state_nodes(self, text: str) -> List[StateNode]:
        """
        Extract predicate names from PDDL precondition / effect text.
        Handles: (not (...)), (and ...), (or ...), (when ...), (forall ...).
        Returns a deduplicated flat list of StateNode objects.
        """
        nodes: List[StateNode] = []
        seen: Set[Tuple[str, bool]] = set()

        # Find all (not (pred ...)) patterns → value=False
        for m in re.finditer(r"\(not\s+\(\s*([\w_]+)", text):
            pred = m.group(1).lower()
            if pred not in ("and", "or", "when", "forall", "exists", "not"):
                key = (pred, False)
                if key not in seen:
                    seen.add(key)
                    nodes.append(StateNode(
                        predicate=pred,
                        value=False,
                        agent_state=pred in self.AGENT_PREDICATES
                    ))

        # Find all positive predicate applications
        # Remove (not (...)) blocks first so we don't double-count
        cleaned = re.sub(r"\(not\s+\([^)]+\)\s*\)", "", text)
        for m in re.finditer(r"\(\s*([\w_]+)", cleaned):
            pred = m.group(1).lower()
            skip = {
                "and", "or", "when", "forall", "exists", "not",
                "define", "domain", "action", "precondition", "effect",
                "parameters", "requirements", "types", "predicates",
                "problem", "objects", "init", "goal"
            }
            if pred in skip:
                continue
            key = (pred, True)
            if key not in seen:
                seen.add(key)
                nodes.append(StateNode(
                    predicate=pred,
                    value=True,
                    agent_state=pred in self.AGENT_PREDICATES
                ))

        return nodes

    def _mark_state_prep_actions(self):
        """
        A state preparation action has:
          - exactly one outgoing effect edge to an AGENT-STATE node
          - no incoming precondition edges from non-trivial state nodes
            (i.e. its only precondition, if any, is also an agent state)

        In VirtualHome: WALK / FIND / TURN_TO / STANDUP all qualify.
        """
        for name, node in self.actions.items():
            agent_effects = [s for s in node.seff if s.agent_state]
            non_agent_preconds = [s for s in node.sdep if not s.agent_state]
            if agent_effects and not non_agent_preconds:
                node.is_state_prep = True

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def sdep(self, action_name: str) -> List[StateNode]:
        """Return precondition state set for action_name."""
        action_name = action_name.lower()
        node = self.actions.get(action_name)
        return node.sdep if node else []

    def seff(self, action_name: str) -> List[StateNode]:
        """Return effect state set for action_name."""
        action_name = action_name.lower()
        node = self.actions.get(action_name)
        return node.seff if node else []

    def is_state_prep_action(self, action_name: str) -> bool:
        action_name = action_name.lower()
        node = self.actions.get(action_name)
        return node.is_state_prep if node else False

    def actions_that_produce(self, predicate: str, value: bool = True) -> List[str]:
        """Return names of actions whose effects include (predicate=value)."""
        result = []
        for name, node in self.actions.items():
            for s in node.seff:
                if s.predicate == predicate.lower() and s.value == value:
                    result.append(name)
                    break
        return result

    def actions_that_require(self, predicate: str, value: bool = True) -> List[str]:
        """Return names of actions whose preconditions include (predicate=value)."""
        return self._state_to_actions.get((predicate.lower(), value), [])

    def preconditions_satisfied(
        self,
        action_name: str,
        env_state: dict
    ) -> Tuple[bool, List[StateNode]]:
        """
        Check whether all SDG preconditions for action_name are satisfied
        against a snapshot of the environment state dict.

        env_state is the dict produced by motion_planner.env_state.to_dict()
        We look for the relevant predicates in:
            env_state["nodes"]  (object states and properties)
            env_state["edges"]  (relations)

        Returns (all_satisfied, list_of_violated_StateNodes).
        Note: This is a lightweight structural check using the SDG predicates.
        The authoritative check is still done by the VirtualHome simulator;
        this is used for diagnosis and lookahead only.
        """
        preconds = self.sdep(action_name)
        violated = []
        node_states = self._collect_node_states(env_state)
        edge_relations = self._collect_edge_relations(env_state)

        for s in preconds:
            pred = s.predicate.lower()
            # Check node-level states
            if pred in node_states:
                present = node_states[pred]
                if s.value != present:
                    violated.append(s)
            # Check edge-level relations
            elif pred in edge_relations:
                present = edge_relations[pred]
                if s.value != present:
                    violated.append(s)
            # Predicate not found in current state snapshot —
            # treat as absent (False); if s.value is True → violated
            elif s.value:
                violated.append(s)

        return (len(violated) == 0, violated)

    def _collect_node_states(self, env_state: dict) -> Dict[str, bool]:
        """Extract {state_name: True/False} from env_state nodes list."""
        result: Dict[str, bool] = {}
        for node in env_state.get("nodes", []):
            for state in node.get("states", []):
                result[state.lower()] = True
            for prop in node.get("properties", []):
                result[prop.lower()] = True
        return result

    def _collect_edge_relations(self, env_state: dict) -> Dict[str, bool]:
        """Extract {relation_type: True} from env_state edges list."""
        result: Dict[str, bool] = {}
        for edge in env_state.get("edges", []):
            rel = edge.get("relation_type", "").lower()
            if rel:
                result[rel] = True
        return result

    def get_all_action_names(self) -> List[str]:
        return list(self.actions.keys())

    def summary(self) -> str:
        lines = [f"StateDependencyGraph  ({len(self.actions)} actions)"]
        for name, node in self.actions.items():
            prep = " [STATE_PREP]" if node.is_state_prep else ""
            lines.append(
                f"  {name}{prep}\n"
                f"    Sdep: {node.sdep}\n"
                f"    Seff: {node.seff}"
            )
        return "\n".join(lines)
