"""
State-Dependency Graph (SDG) for VirtualHome.
Parses the PDDL domain file and builds a structured graph of
action preconditions and effects used for error diagnosis and replanning.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StateCondition:
    """A single predicate condition: (predicate, args, negated)."""
    predicate: str
    args: List[str]          # symbolic param names, e.g. ["?char", "?obj"]
    negated: bool = False

    def __repr__(self):
        neg = "NOT " if self.negated else ""
        return f"{neg}{self.predicate}({', '.join(self.args)})"


@dataclass
class ActionSchema:
    """Preconditions and effects for one PDDL action."""
    name: str
    parameters: List[Tuple[str, str]]      # [("?char", "character"), ("?obj", "object")]
    preconditions: List[StateCondition] = field(default_factory=list)
    effects: List[StateCondition] = field(default_factory=list)
    is_state_preparation: bool = False     # True if action only sets up agent state (e.g. walk)

    def __repr__(self):
        return (
            f"ActionSchema({self.name}, "
            f"pre={self.preconditions}, "
            f"eff={self.effects})"
        )


# ---------------------------------------------------------------------------
# PDDL Parser
# ---------------------------------------------------------------------------

class PDDLParser:
    """
    Lightweight PDDL parser for VirtualHome domain file.
    Extracts action schemas with their parameters, preconditions, and effects.
    """

    def __init__(self, pddl_path: str):
        self.pddl_path = pddl_path
        self.actions: Dict[str, ActionSchema] = {}
        self._parse()

    def _parse(self):
        with open(self.pddl_path, "r") as f:
            content = f.read()

        # Remove comments
        content = re.sub(r";.*", "", content)

        # Extract each :action block
        action_blocks = re.findall(
            r"\(:action\s+([\w_-]+)(.*?)(?=\(:action|\)$|\Z)",
            content,
            re.DOTALL,
        )
        for action_name, action_body in action_blocks:
            action_name = action_name.strip()
            schema = self._parse_action(action_name, action_body)
            if schema:
                self.actions[action_name] = schema

    def _parse_action(self, name: str, body: str) -> Optional[ActionSchema]:
        # Parameters
        params = self._extract_section(body, ":parameters")
        parameters = self._parse_parameters(params)

        # Preconditions
        pre_str = self._extract_section(body, ":precondition")
        preconditions = self._parse_conditions(pre_str)

        # Effects
        eff_str = self._extract_section(body, ":effect")
        effects = self._parse_conditions(eff_str)

        schema = ActionSchema(
            name=name,
            parameters=parameters,
            preconditions=preconditions,
            effects=effects,
        )
        return schema

    def _extract_section(self, body: str, keyword: str) -> str:
        """Extract the balanced parentheses block after a keyword."""
        idx = body.find(keyword)
        if idx == -1:
            return ""
        # Find the opening paren
        start = body.find("(", idx)
        if start == -1:
            return ""
        depth = 0
        for i in range(start, len(body)):
            if body[i] == "(":
                depth += 1
            elif body[i] == ")":
                depth -= 1
                if depth == 0:
                    return body[start : i + 1]
        return body[start:]

    def _parse_parameters(self, params_str: str) -> List[Tuple[str, str]]:
        """Parse '(?char - character ?obj - object)' into [('?char','character'),...]"""
        params_str = params_str.strip("()")
        result = []
        tokens = params_str.split()
        i = 0
        while i < len(tokens):
            if tokens[i].startswith("?"):
                var = tokens[i]
                if i + 2 < len(tokens) and tokens[i + 1] == "-":
                    typ = tokens[i + 2]
                    result.append((var, typ))
                    i += 3
                else:
                    result.append((var, "object"))
                    i += 1
            else:
                i += 1
        return result

    def _parse_conditions(self, cond_str: str) -> List[StateCondition]:
        """Flatten a condition expression into a list of StateCondition objects."""
        conditions = []
        if not cond_str or cond_str.strip() in ("()", ""):
            return conditions
        self._extract_atoms(cond_str, conditions, negated=False)
        return conditions

    def _extract_atoms(self, expr: str, out: List[StateCondition], negated: bool):
        """Recursively walk expression and collect atomic predicates."""
        expr = expr.strip()
        if not expr or expr in ("()", ""):
            return

        # strip outer parens
        if expr.startswith("(") and expr.endswith(")"):
            inner = expr[1:-1].strip()
        else:
            inner = expr

        tokens = self._tokenize(inner)
        if not tokens:
            return

        head = tokens[0].lower()

        if head == "and":
            for sub in tokens[1:]:
                self._extract_atoms(sub, out, negated)

        elif head == "not":
            if len(tokens) > 1:
                self._extract_atoms(tokens[1], out, not negated)

        elif head in ("or", "when", "forall", "exists", "imply"):
            # For simplicity, recurse into sub-expressions collecting conditions
            for sub in tokens[1:]:
                self._extract_atoms(sub, out, negated)

        else:
            # Atomic predicate: head is predicate name, rest are args
            args = [t.strip("()") for t in tokens[1:] if not t.startswith("-")]
            out.append(StateCondition(predicate=head, args=args, negated=negated))

    def _tokenize(self, expr: str) -> List[str]:
        """Split expression into top-level tokens respecting parentheses."""
        tokens = []
        depth = 0
        current = []
        for ch in expr:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
                if depth == 0:
                    tokens.append("".join(current).strip())
                    current = []
            elif ch in (" ", "\t", "\n") and depth == 0:
                if current:
                    tokens.append("".join(current).strip())
                    current = []
            else:
                current.append(ch)
        if current:
            tokens.append("".join(current).strip())
        return [t for t in tokens if t]


# ---------------------------------------------------------------------------
# State-Dependency Graph
# ---------------------------------------------------------------------------

class StateDependencyGraph:
    """
    The SDG built from the VirtualHome PDDL domain.

    Nodes:
      - Action nodes: one per PDDL action
      - State nodes: one per unique predicate name

    Edges:
      - action → state  (effect edge): action sets state
      - state → action  (dependency edge): action requires state
    """

    # Actions that only update agent position/posture with no incoming state deps.
    # These are 'state preparation actions' in SDA-Planner terminology.
    STATE_PREP_ACTIONS = {"walk_towards", "walk_into", "standup", "turn_to"}

    def __init__(self, pddl_path: str):
        self.parser = PDDLParser(pddl_path)
        self.actions: Dict[str, ActionSchema] = self.parser.actions
        self._mark_state_preparation_actions()

        # Pre-computed lookup structures
        # predicate → set of actions that produce it (as effect)
        self.producers: Dict[str, Set[str]] = {}
        # predicate → set of actions that require it (as precondition)
        self.consumers: Dict[str, Set[str]] = {}
        self._build_indexes()

    def _mark_state_preparation_actions(self):
        for name, schema in self.actions.items():
            if name in self.STATE_PREP_ACTIONS:
                schema.is_state_preparation = True

    def _build_indexes(self):
        for action_name, schema in self.actions.items():
            for cond in schema.effects:
                self.producers.setdefault(cond.predicate, set()).add(action_name)
            for cond in schema.preconditions:
                self.consumers.setdefault(cond.predicate, set()).add(action_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_preconditions(self, action_name: str) -> List[StateCondition]:
        """Return the precondition list for an action."""
        schema = self.actions.get(action_name)
        return schema.preconditions if schema else []

    def get_effects(self, action_name: str) -> List[StateCondition]:
        """Return the effect list for an action."""
        schema = self.actions.get(action_name)
        return schema.effects if schema else []

    def is_state_prep(self, action_name: str) -> bool:
        """True if this is a state preparation action (e.g. walk_towards)."""
        schema = self.actions.get(action_name)
        return schema.is_state_preparation if schema else False

    def get_producers(self, predicate: str) -> Set[str]:
        """Actions that produce (set) the given predicate as an effect."""
        return self.producers.get(predicate, set())

    def get_consumers(self, predicate: str) -> Set[str]:
        """Actions that require the given predicate as a precondition."""
        return self.consumers.get(predicate, set())

    def get_required_prep_actions(self, action_name: str) -> List[str]:
        """
        Return the state preparation actions needed before action_name.
        E.g. grab requires next_to → walk_towards is the prep action.
        """
        preps = []
        for cond in self.get_preconditions(action_name):
            for producer in self.get_producers(cond.predicate):
                if self.is_state_prep(producer):
                    preps.append(producer)
        return list(set(preps))

    def check_preconditions(
        self,
        action_name: str,
        current_state: Dict,
        char_id: int,
        obj_id: Optional[int] = None,
    ) -> Tuple[bool, List[StateCondition]]:
        """
        Check whether the preconditions for action_name are met in current_state.
        Returns (all_satisfied, list_of_unsatisfied_conditions).
        
        current_state: dict from motion_planner.env_state.to_dict()
        """
        unsatisfied = []
        preconditions = self.get_preconditions(action_name)

        for cond in preconditions:
            satisfied = self._check_single_condition(
                cond, current_state, char_id, obj_id
            )
            if not satisfied:
                unsatisfied.append(cond)

        return len(unsatisfied) == 0, unsatisfied

    def _check_single_condition(
        self,
        cond: StateCondition,
        state: Dict,
        char_id: int,
        obj_id: Optional[int],
    ) -> bool:
        """
        Heuristic check of a single condition against VirtualHome env state dict.
        The env state dict has keys like 'nodes' (list of node dicts with 'states')
        and 'edges' (list of edge dicts).
        """
        pred = cond.predicate
        nodes = {n["id"]: n for n in state.get("nodes", [])}
        edges = state.get("edges", [])

        # Character-level checks
        if pred == "sitting":
            char_node = nodes.get(char_id, {})
            has_sitting = "SITTING" in char_node.get("states", [])
            return has_sitting if not cond.negated else not has_sitting

        if pred == "lying":
            char_node = nodes.get(char_id, {})
            has_lying = "LYING" in char_node.get("states", [])
            return has_lying if not cond.negated else not has_lying

        # Object-level checks
        if obj_id is not None:
            obj_node = nodes.get(obj_id, {})
            obj_states = obj_node.get("states", [])

            state_map = {
                "closed": "CLOSED",
                "open": "OPEN",
                "on": "ON",
                "off": "OFF",
                "plugged_in": "PLUGGED_IN",
                "plugged_out": "PLUGGED_OUT",
                "clean": "CLEAN",
                "dirty": "DIRTY",
                "grabbable": "GRABBABLE",
                "can_open": "CAN_OPEN",
                "has_switch": "HAS_SWITCH",
                "has_plug": "HAS_PLUG",
                "sittable": "SITTABLE",
                "lieable": "LIEABLE",
                "lookable": "LOOKABLE",
                "movable": "MOVABLE",
            }

            if pred in state_map:
                has_state = state_map[pred] in obj_states
                return has_state if not cond.negated else not has_state

        # Relational checks
        if pred == "next_to":
            exists = any(
                e["from_id"] == char_id
                and e["to_id"] == obj_id
                and e["relation_type"] == "CLOSE"
                for e in edges
            )
            return exists if not cond.negated else not exists

        if pred in ("holds_lh", "holds_rh"):
            relation = "HOLDS_LH" if pred == "holds_lh" else "HOLDS_RH"
            exists = any(
                e["from_id"] == char_id
                and e["to_id"] == obj_id
                and e["relation_type"] == relation
                for e in edges
            )
            return exists if not cond.negated else not exists

        if pred == "obj_inside":
            exists = any(
                e["to_id"] == obj_id and e["relation_type"] == "INSIDE"
                for e in edges
            )
            return exists if not cond.negated else not exists

        # Default: assume satisfied if we can't check
        return True

    def summarize(self) -> str:
        lines = [f"StateDependencyGraph: {len(self.actions)} actions loaded"]
        for name, schema in sorted(self.actions.items()):
            prep = " [STATE-PREP]" if schema.is_state_preparation else ""
            lines.append(f"  {name}{prep}")
            lines.append(f"    PRE: {schema.preconditions}")
            lines.append(f"    EFF: {schema.effects}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_sdg(pddl_path: str) -> StateDependencyGraph:
    """Build and return a StateDependencyGraph from the given PDDL file."""
    return StateDependencyGraph(pddl_path)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "examples/virtualhome.pddl"
    sdg = build_sdg(path)
    print(sdg.summarize())