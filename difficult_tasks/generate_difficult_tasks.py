"""
generate_difficult_tasks.py
===========================
Generates 50 DIFFICULT action-sequencing tasks (ids 9001_1 .. 9050_1) for
scene 1, in exactly the same file structure as the original EAI dataset, and
VALIDATES each one by executing an authored gold script in the simulator and
checking every goal.

Design goal: stress the SDA feedback loop. Every task has >= 4 substantive
goals and at least one executor-verified failure lever:

  * items locked INSIDE closed containers (GRAB fails until OPEN)
  * OPEN requires a FREE HAND (character starts holding items)
  * character starts SITTING (WALK fails until STANDUP)
  * mixed goal relations (ON -> PUTBACK vs INSIDE -> PUTIN)
  * containers must be CLOSED again at the end (restore-state goals)
  * appliances must end CLOSED + ON (load -> close -> switch on discipline)
  * duplicate-class instances with per-ID toggle goals (6 lights)
  * PLUGGED_OUT devices that must be plugged in before switching on

Families:
  A 9001-9010  "Busy hands"        base 419_2  (held items + locked cutlery)
  B 9011-9020  "Appliance load"    base 954_2 / 826_1 (INSIDE machine + close + on)
  C 9021-9030  "Scatter & restore" base 826_1  (many containers -> table, re-close)
  D 9031-9040  "Precision toggles" base 232_2  (6 lights, shaver, clock, TV)
  E 9041-9050  "Rough morning"     base 286_2  (sitting + held + box/freezer + console)

Usage:
  python3 difficult_tasks/generate_difficult_tasks.py          # generate + validate
"""

import copy
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

DATASET = os.path.join(
    REPO, "src/virtualhome_eval/dataset/programs_processed_precond_nograb_morepreconds")
GRAPH_DIR = os.path.join(
    DATASET, "init_and_final_graphs/TrimmedTestScene1_graph/results_intentions_march-13-18")
SCRIPT_DIR = os.path.join(
    DATASET, "executable_programs/TrimmedTestScene1_graph/results_intentions_march-13-18")
OUT_RES = os.path.join(REPO, "difficult_tasks/resources/virtualhome")

CHAR = 65
COUCH = 352

ROOM_CAT = "Rooms"


# ─────────────────────────────────────────────────────────────────────────────
# Graph mutation helpers
# ─────────────────────────────────────────────────────────────────────────────

def node_of(g, oid):
    for n in g["nodes"]:
        if n["id"] == oid:
            return n
    raise KeyError(f"node {oid} not in graph")


def room_of(g, oid):
    """Room id that oid is INSIDE of (category Rooms)."""
    rooms = {n["id"] for n in g["nodes"] if n.get("category") == ROOM_CAT}
    for e in g["edges"]:
        if e["from_id"] == oid and e["relation_type"] == "INSIDE" and e["to_id"] in rooms:
            return e["to_id"]
    return None


def strip_placement(g, oid):
    """Remove oid's non-room placement edges (ON / INSIDE-nonroom / CLOSE, both
    directions, and any HOLDS edges pointing at it)."""
    rooms = {n["id"] for n in g["nodes"] if n.get("category") == ROOM_CAT}
    keep = []
    for e in g["edges"]:
        drop = False
        if e["from_id"] == oid:
            if e["relation_type"] in ("ON", "CLOSE", "FACING"):
                drop = True
            if e["relation_type"] == "INSIDE" and e["to_id"] not in rooms:
                drop = True
        if e["to_id"] == oid and e["relation_type"] in ("ON", "CLOSE", "HOLDS_RH", "HOLDS_LH"):
            drop = True
        if not drop:
            keep.append(e)
    g["edges"] = keep


def set_room(g, oid, room_id):
    rooms = {n["id"] for n in g["nodes"] if n.get("category") == ROOM_CAT}
    g["edges"] = [e for e in g["edges"]
                  if not (e["from_id"] == oid and e["relation_type"] == "INSIDE"
                          and e["to_id"] in rooms)]
    if room_id is not None:
        g["edges"].append({"from_id": oid, "relation_type": "INSIDE", "to_id": room_id})


def set_states(g, oid, add=(), remove=()):
    n = node_of(g, oid)
    st = [s for s in n["states"] if s not in remove]
    for a in add:
        if a not in st:
            st.append(a)
    n["states"] = st


COMPLEMENT = {"ON": "OFF", "OFF": "ON", "OPEN": "CLOSED", "CLOSED": "OPEN",
              "PLUGGED_IN": "PLUGGED_OUT", "PLUGGED_OUT": "PLUGGED_IN"}


def apply_state_goal(g, oid, state):
    set_states(g, oid, add=[state], remove=[COMPLEMENT.get(state, "")])


def move_inside(g, oid, cid):
    """Put oid INSIDE container cid; container becomes CLOSED."""
    strip_placement(g, oid)
    g["edges"].append({"from_id": oid, "relation_type": "INSIDE", "to_id": cid})
    r = room_of(g, cid)
    set_room(g, oid, r)
    apply_state_goal(g, cid, "CLOSED")


def hold(g, oid, hand):
    strip_placement(g, oid)
    g["edges"].append({"from_id": CHAR, "relation_type": f"HOLDS_{hand}", "to_id": oid})
    set_room(g, oid, room_of(g, CHAR))


def sit_char(g):
    set_states(g, CHAR, add=["SITTING"])
    g["edges"].append({"from_id": CHAR, "relation_type": "ON", "to_id": COUCH})


def place_on(g, oid, tid):
    strip_placement(g, oid)
    g["edges"].append({"from_id": oid, "relation_type": "ON", "to_id": tid})
    g["edges"].append({"from_id": oid, "relation_type": "CLOSE", "to_id": tid})
    g["edges"].append({"from_id": tid, "relation_type": "CLOSE", "to_id": oid})
    set_room(g, oid, room_of(g, tid))


def place_in(g, oid, cid):
    strip_placement(g, oid)
    g["edges"].append({"from_id": oid, "relation_type": "INSIDE", "to_id": cid})
    set_room(g, oid, room_of(g, cid))


# ─────────────────────────────────────────────────────────────────────────────
# Task spec → files
# ─────────────────────────────────────────────────────────────────────────────

class Spec:
    def __init__(self, tid, name, desc, base,
                 stash=(),        # [(item, container), ...] item starts INSIDE closed container
                 held=(),         # [(item, 'RH'/'LH'), ...] char starts holding item
                 sit=False,       # char starts SITTING on couch
                 init_states=(),  # [(oid, state), ...] extra initial state overrides
                 goals_on=(),     # [(item, surface)]   -> PUTBACK, edge goal ON
                 goals_in=(),     # [(item, container)] -> PUTIN, edge goal INSIDE
                 goal_states=(),  # [(oid, state)]      -> node goals (order = gold order)
                 ):
        self.tid, self.name, self.desc, self.base = tid, name, desc, base
        self.stash, self.held, self.sit = list(stash), list(held), sit
        self.init_states = list(init_states)
        self.goals_on, self.goals_in = list(goals_on), list(goals_in)
        self.goal_states = list(goal_states)

    # design rule: a held item must have an ON goal, unless it is the ONLY
    # held item (one free hand is needed to OPEN a container).
    def check(self, g):
        held_ids = [o for o, _ in self.held]
        on_ids = [o for o, _ in self.goals_on]
        if len(held_ids) > 1:
            for o in held_ids:
                assert o in on_ids, f"{self.tid}: held {o} must have ON goal (2 held)"
        for o, _ in self.stash + self.held + self.goals_on + self.goals_in:
            pass
        for o, _ in list(self.goals_on) + list(self.goals_in):
            props = node_of(g, o)["properties"]
            assert "GRABBABLE" in props, f"{self.tid}: {o} not GRABBABLE"


def build_graphs(spec):
    base = json.load(open(os.path.join(GRAPH_DIR, f"file{spec.base}.json")))
    init = copy.deepcopy(base["init_graph"])

    spec.check(init)

    for oid, cid in spec.stash:
        move_inside(init, oid, cid)
    for oid, hand in spec.held:
        hold(init, oid, hand)
    if spec.sit:
        sit_char(init)
    for oid, state in spec.init_states:
        apply_state_goal(init, oid, state)

    final = copy.deepcopy(init)
    # character back to neutral
    final["edges"] = [e for e in final["edges"]
                      if not (e["from_id"] == CHAR and e["relation_type"]
                              in ("HOLDS_RH", "HOLDS_LH", "ON"))]
    set_states(final, CHAR, remove=["SITTING"])
    for oid, tid in spec.goals_on:
        place_on(final, oid, tid)
    for oid, cid in spec.goals_in:
        place_in(final, oid, cid)
    for oid, state in spec.goal_states:
        apply_state_goal(final, oid, state)

    return init, final


def build_goals(spec, init):
    goals = []
    for oid, state in spec.goal_states:
        goals.append({"id": oid, "class_name": node_of(init, oid)["class_name"],
                      "state": state})
    for oid, tid in spec.goals_on:
        goals.append({"from_id": oid, "relation_type": "ON", "to_id": tid})
    for oid, cid in spec.goals_in:
        goals.append({"from_id": oid, "relation_type": "INSIDE", "to_id": cid})
    return goals


def build_gold(spec, init):
    """Author a gold plan (also validates task feasibility when simulated)."""
    name = {n["id"]: n["class_name"] for n in init["nodes"]}
    A = lambda act, *objs: "[{}] {}".format(
        act, " ".join(f"<{name[o]}> (1.{o})" for o in objs))
    steps = []
    in_goal = dict(spec.goals_in)
    on_goal = dict(spec.goals_on)
    goal_states = dict(spec.goal_states)

    opened = set()

    def ensure_open(cid):
        if cid in opened:
            return
        n = node_of(init, cid)
        if "CAN_OPEN" in n["properties"]:
            if "CLOSED" in n["states"]:
                steps.append(A("WALK", cid))
                steps.append(A("OPEN", cid))
            opened.add(cid)  # already open counts as opened (no emit)

    if spec.sit:
        steps.append("[STANDUP]")

    # 1. unload held items to their goal destinations
    for oid, _ in spec.held:
        if oid in on_goal:
            t = on_goal.pop(oid)
            steps.append(A("WALK", t))
            steps.append(A("PUTBACK", oid, t))
        else:
            c = in_goal.pop(oid)
            ensure_open(c)
            steps.append(A("PUTIN", oid, c))

    # 2. open every source container and every INSIDE-goal destination
    for _, cid in spec.stash:
        ensure_open(cid)
    for _, cid in spec.goals_in:
        ensure_open(cid)

    # 3. fetch and place remaining items
    for oid, t in list(on_goal.items()):
        steps.append(A("WALK", oid))
        steps.append(A("GRAB", oid))
        steps.append(A("WALK", t))
        steps.append(A("PUTBACK", oid, t))
    for oid, c in list(in_goal.items()):
        steps.append(A("WALK", oid))
        steps.append(A("GRAB", oid))
        steps.append(A("WALK", c))
        steps.append(A("PUTIN", oid, c))

    # 4. close everything that must end CLOSED
    for oid, state in spec.goal_states:
        if state == "CLOSED":
            steps.append(A("WALK", oid))
            steps.append(A("CLOSE", oid))

    # 5. device toggles (PLUGGED_IN before ON, per spec order); skip actions
    # whose goal state already holds initially (executor rejects no-ops)
    init_states_of = lambda oid: node_of(init, oid)["states"]
    for oid, state in spec.goal_states:
        if state == "PLUGGED_IN" and "PLUGGED_IN" not in init_states_of(oid):
            steps.append(A("WALK", oid))
            steps.append(A("PLUGIN", oid))
        elif state == "ON" and "ON" not in init_states_of(oid):
            steps.append(A("WALK", oid))
            steps.append(A("SWITCHON", oid))
        elif state == "OFF" and "OFF" not in init_states_of(oid):
            steps.append(A("WALK", oid))
            steps.append(A("SWITCHOFF", oid))

    return steps


# ─────────────────────────────────────────────────────────────────────────────
# The 50 specs
# ─────────────────────────────────────────────────────────────────────────────

def make_specs():
    S = []
    T = 355   # table
    CUP = 229  # cupboard
    FRZ = 289  # freezer
    TV = 410
    L245, L411 = 245, 411

    # ── Family A: Busy hands (base 419_2) ───────────────────────────────────
    # items: cloth_napkin 1001, plate 1002, knife 1003, fork 1004, cup 1005
    NAP, PLT, KNF, FRK, CP = 1001, 1002, 1003, 1004, 1005
    A = "Hard busy hands"
    ad = "Both hands start full; put everything on the table and re-close containers."
    S += [
        Spec("9001_1", A, ad, "419_2", held=[(NAP, "RH"), (CP, "LH")],
             stash=[(PLT, CUP), (KNF, CUP), (FRK, CUP)],
             goals_on=[(NAP, T), (CP, T), (PLT, T), (KNF, T), (FRK, T)],
             goal_states=[(CUP, "CLOSED")]),
        Spec("9002_1", A, ad, "419_2", held=[(NAP, "RH"), (FRK, "LH")],
             stash=[(PLT, CUP), (KNF, CUP), (CP, FRZ)],
             goals_on=[(NAP, T), (FRK, T), (PLT, T), (KNF, T), (CP, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9003_1", A, ad, "419_2", held=[(CP, "RH"), (KNF, "LH")],
             stash=[(NAP, FRZ), (PLT, FRZ), (FRK, FRZ)],
             goals_on=[(CP, T), (KNF, T), (NAP, T), (PLT, T), (FRK, T)],
             goal_states=[(FRZ, "CLOSED"), (TV, "ON")]),
        Spec("9004_1", A, ad, "419_2", held=[(PLT, "RH"), (CP, "LH")],
             stash=[(NAP, CUP), (KNF, CUP), (FRK, CUP)],
             goals_on=[(PLT, T), (CP, T), (NAP, T), (KNF, T), (FRK, T)],
             goal_states=[(CUP, "CLOSED"), (TV, "ON")]),
        Spec("9005_1", A, ad, "419_2", held=[(NAP, "RH"), (CP, "LH")],
             stash=[(PLT, CUP), (KNF, FRZ), (FRK, CUP)],
             goals_on=[(NAP, T), (CP, T), (PLT, T), (KNF, T), (FRK, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9006_1", A, ad, "419_2", held=[(NAP, "RH"), (CP, "LH")], sit=True,
             stash=[(PLT, CUP), (KNF, CUP), (FRK, CUP)],
             goals_on=[(NAP, T), (CP, T), (PLT, T), (KNF, T), (FRK, T)],
             goal_states=[(CUP, "CLOSED")]),
        Spec("9007_1", A, ad, "419_2", held=[(NAP, "RH"), (FRK, "LH")], sit=True,
             stash=[(PLT, CUP), (KNF, CUP), (CP, FRZ)],
             goals_on=[(NAP, T), (FRK, T), (PLT, T), (KNF, T), (CP, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9008_1", A, ad, "419_2", held=[(FRK, "RH"), (KNF, "LH")], sit=True,
             stash=[(NAP, CUP), (PLT, CUP), (CP, CUP)],
             goals_on=[(FRK, T), (KNF, T), (NAP, T), (PLT, T), (CP, T)],
             goal_states=[(CUP, "CLOSED"), (TV, "ON")]),
        Spec("9009_1", A, ad, "419_2", held=[(NAP, "RH"), (PLT, "LH")],
             stash=[(KNF, FRZ), (FRK, FRZ), (CP, CUP)],
             goals_on=[(NAP, T), (PLT, T), (KNF, T), (FRK, T), (CP, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (TV, "ON")]),
        Spec("9010_1", A, ad, "419_2", held=[(CP, "RH"), (NAP, "LH")], sit=True,
             stash=[(PLT, FRZ), (KNF, FRZ), (FRK, FRZ)],
             goals_on=[(CP, T), (NAP, T), (PLT, T), (KNF, T), (FRK, T)],
             goal_states=[(FRZ, "CLOSED"), (TV, "ON")]),
    ]

    # ── Family B: Appliance load (bases 954_2, 826_1) ───────────────────────
    WM = 1001  # washing machine (954_2)
    BSK = 1000  # basket (954_2)
    DRS, PNT, SHT, UND, SOAP = 1002, 1003, 1004, 1005, 1006
    B = "Hard appliance load"
    bd = "Load every item INSIDE the appliance, close it, switch it on, and re-close all containers."
    S += [
        Spec("9011_1", B, bd, "954_2",
             # PLUGGED_OUT at init so the PLUGGED_IN goal is earned, not free
             init_states=[(WM, "PLUGGED_OUT")],
             stash=[(DRS, BSK), (PNT, BSK), (SHT, BSK), (UND, BSK), (SOAP, CUP)],
             goals_in=[(DRS, WM), (PNT, WM), (SHT, WM), (UND, WM), (SOAP, WM)],
             goal_states=[(BSK, "CLOSED"), (CUP, "CLOSED"), (WM, "CLOSED"),
                          (WM, "PLUGGED_IN"), (WM, "ON")]),
        Spec("9012_1", B, bd, "954_2",
             stash=[(DRS, BSK), (PNT, BSK), (SHT, CUP), (UND, CUP), (SOAP, CUP)],
             goals_in=[(DRS, WM), (PNT, WM), (SHT, WM), (UND, WM), (SOAP, WM)],
             goal_states=[(BSK, "CLOSED"), (CUP, "CLOSED"), (WM, "CLOSED"), (WM, "ON")]),
        Spec("9013_1", B, bd, "954_2", init_states=[(WM, "OPEN")],
             # Scene quirk: in base 954_2 the clothes START INSIDE the washing
             # machine, which made 4/5 INSIDE goals free. Stash them in the
             # basket so loading is earned; the task's distinguishing lever
             # stays "the machine is already open" (no OPEN-the-washer step).
             stash=[(DRS, BSK), (PNT, BSK), (SHT, BSK), (UND, BSK), (SOAP, CUP)],
             goals_in=[(DRS, WM), (PNT, WM), (SHT, WM), (UND, WM), (SOAP, WM)],
             goal_states=[(CUP, "CLOSED"), (WM, "CLOSED"), (WM, "ON")]),
        Spec("9014_1", B, bd, "954_2", sit=True,
             stash=[(DRS, BSK), (PNT, BSK), (SHT, BSK), (UND, BSK), (SOAP, CUP)],
             goals_in=[(DRS, WM), (PNT, WM), (SHT, WM), (UND, WM), (SOAP, WM)],
             goal_states=[(BSK, "CLOSED"), (CUP, "CLOSED"), (WM, "CLOSED"), (WM, "ON")]),
        Spec("9015_1", B, bd, "954_2", held=[(PNT, "RH")],
             stash=[(DRS, BSK), (SHT, BSK), (UND, BSK), (SOAP, CUP)],
             goals_in=[(PNT, WM), (DRS, WM), (SHT, WM), (UND, WM), (SOAP, WM)],
             goal_states=[(BSK, "CLOSED"), (CUP, "CLOSED"), (WM, "CLOSED"), (WM, "ON")]),
    ]
    DW = 1000  # dishwasher (826_1)
    NS = 102   # nightstand
    OVN = 295
    P1, P2, P3, P4 = 1003, 1004, 1005, 1006   # plates
    C1, C2, C3, C4 = 1007, 1008, 1009, 1010   # cups
    F1, F2 = 1001, 1002                        # forks
    K1 = 1011                                  # knife
    SP1, SP2 = 1013, 1014                      # spoons
    DSOAP = 1016
    S += [
        Spec("9016_1", B, bd, "826_1",
             stash=[(P1, CUP), (P2, CUP), (C1, CUP), (C2, CUP), (DSOAP, NS)],
             goals_in=[(P1, DW), (P2, DW), (C1, DW), (C2, DW), (DSOAP, DW)],
             goal_states=[(CUP, "CLOSED"), (NS, "CLOSED"), (DW, "CLOSED"), (DW, "ON")]),
        Spec("9017_1", B, bd, "826_1",
             stash=[(F1, FRZ), (F2, FRZ), (SP1, FRZ), (SP2, FRZ), (K1, OVN)],
             goals_in=[(F1, DW), (F2, DW), (SP1, DW), (SP2, DW), (K1, DW)],
             goal_states=[(FRZ, "CLOSED"), (OVN, "CLOSED"), (DW, "CLOSED"), (DW, "ON")]),
        Spec("9018_1", B, bd, "826_1",
             stash=[(P3, NS), (P4, NS), (C3, CUP), (C4, CUP), (DSOAP, FRZ)],
             goals_in=[(P3, DW), (P4, DW), (C3, DW), (C4, DW), (DSOAP, DW)],
             goal_states=[(NS, "CLOSED"), (CUP, "CLOSED"), (FRZ, "CLOSED"),
                          (DW, "CLOSED"), (DW, "ON")]),
        Spec("9019_1", B, bd, "826_1", sit=True,
             stash=[(P1, CUP), (P2, CUP), (C1, CUP), (C2, CUP), (DSOAP, NS)],
             goals_in=[(P1, DW), (P2, DW), (C1, DW), (C2, DW), (DSOAP, DW)],
             goal_states=[(CUP, "CLOSED"), (NS, "CLOSED"), (DW, "CLOSED"), (DW, "ON")]),
        Spec("9020_1", B, bd, "826_1",
             stash=[(P1, CUP), (P2, CUP), (P3, CUP), (P4, CUP),
                    (C1, FRZ), (C2, FRZ), (F1, NS), (SP1, NS)],
             goals_in=[(P1, DW), (P2, DW), (P3, DW), (P4, DW),
                       (C1, DW), (C2, DW), (F1, DW), (SP1, DW)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (NS, "CLOSED"),
                          (DW, "CLOSED"), (DW, "ON")]),
    ]

    # ── Family C: Scatter & restore (base 826_1) ────────────────────────────
    C_ = "Hard scatter restore"
    cd = "Collect items from several closed containers onto the table, then close everything again."
    JKT, CUP6, BWL = 2005, 2006, 2007
    S += [
        Spec("9021_1", C_, cd, "826_1",
             stash=[(P1, CUP), (P2, CUP), (P3, CUP), (C1, FRZ), (C2, FRZ)],
             goals_on=[(P1, T), (P2, T), (P3, T), (C1, T), (C2, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9022_1", C_, cd, "826_1",
             stash=[(F1, NS), (F2, NS), (K1, NS), (SP1, NS)],
             goals_on=[(F1, T), (F2, T), (K1, T), (SP1, T)],
             goal_states=[(NS, "CLOSED"), (TV, "ON")]),
        Spec("9023_1", C_, cd, "826_1",
             stash=[(C1, OVN), (C2, OVN), (C3, OVN), (C4, OVN)],
             goals_on=[(C1, T), (C2, T), (C3, T), (C4, T)],
             goal_states=[(OVN, "CLOSED")]),
        Spec("9024_1", C_, cd, "826_1", sit=True,
             stash=[(P1, CUP), (P2, CUP), (P3, CUP), (C1, FRZ), (C2, FRZ)],
             goals_on=[(P1, T), (P2, T), (P3, T), (C1, T), (C2, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (TV, "ON")]),
        Spec("9025_1", C_, cd, "826_1", init_states=[(L245, "OFF")],
             stash=[(SP1, FRZ), (SP2, FRZ), (P1, CUP)],
             goals_on=[(SP1, T), (SP2, T), (P1, T)],
             goal_states=[(FRZ, "CLOSED"), (CUP, "CLOSED"), (L245, "ON")]),
        Spec("9026_1", C_, cd, "826_1", held=[(CUP6, "RH")],
             stash=[(P1, CUP), (P2, CUP)],
             goals_on=[(CUP6, T), (P1, T), (P2, T)],
             goal_states=[(CUP, "CLOSED"), (TV, "ON")]),
        Spec("9027_1", C_, cd, "826_1",
             stash=[(P1, CUP), (C1, FRZ), (F1, NS), (K1, NS), (SP1, FRZ), (P2, CUP)],
             goals_on=[(P1, T), (C1, T), (F1, T), (K1, T), (SP1, T), (P2, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (NS, "CLOSED")]),
        Spec("9028_1", C_, cd, "826_1",
             stash=[(BWL, CUP), (P1, CUP), (P2, CUP)],
             goals_on=[(BWL, T), (P1, T), (P2, T)],
             goal_states=[(CUP, "CLOSED"), (OVN, "ON")]),
        Spec("9029_1", C_, cd, "826_1", sit=True, init_states=[(L245, "OFF")],
             stash=[(C1, OVN), (C2, OVN), (C3, OVN), (C4, OVN)],
             goals_on=[(C1, T), (C2, T), (C3, T), (C4, T)],
             goal_states=[(OVN, "CLOSED"), (L245, "ON")]),
        Spec("9030_1", C_, cd, "826_1",
             stash=[(P1, CUP), (P2, CUP), (P3, CUP), (C1, FRZ), (C2, FRZ),
                    (F1, NS), (SP1, NS), (K1, NS)],
             goals_on=[(P1, T), (P2, T), (P3, T), (C1, T), (C2, T),
                       (F1, T), (SP1, T), (K1, T)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (NS, "CLOSED"), (TV, "ON")]),
    ]

    # ── Family D: Precision toggles (base 232_2) ────────────────────────────
    # lights: 64 ON, 169 ON, 245 OFF, 411 ON, 1000 OFF, 1001 OFF
    D = "Hard precision toggles"
    dd = "Set every listed device to exactly the requested state; identical devices differ only by ID."
    SHV, CLK = 2002, 2019
    S += [
        Spec("9031_1", D, dd, "232_2",
             goal_states=[(245, "ON"), (1000, "ON"), (411, "OFF"), (64, "OFF")]),
        Spec("9032_1", D, dd, "232_2",
             goal_states=[(1000, "ON"), (1001, "ON"), (169, "OFF"), (TV, "ON")]),
        Spec("9033_1", D, dd, "232_2", init_states=[(SHV, "OFF")],
             goal_states=[(SHV, "PLUGGED_IN"), (SHV, "ON"), (245, "ON"), (411, "OFF")]),
        Spec("9034_1", D, dd, "232_2",
             goal_states=[(CLK, "OFF"), (64, "OFF"), (169, "OFF"), (411, "OFF"), (1000, "ON")]),
        Spec("9035_1", D, dd, "232_2",
             goal_states=[(TV, "ON"), (245, "ON"), (1001, "ON"), (CLK, "OFF")]),
        Spec("9036_1", D, dd, "232_2", sit=True,
             goal_states=[(245, "ON"), (1000, "ON"), (411, "OFF"), (64, "OFF")]),
        Spec("9037_1", D, dd, "232_2", init_states=[(SHV, "OFF")],
             goal_states=[(SHV, "PLUGGED_IN"), (SHV, "ON"), (TV, "ON"),
                          (411, "OFF"), (169, "OFF")]),
        Spec("9038_1", D, dd, "232_2",
             goal_states=[(245, "ON"), (1000, "ON"), (1001, "ON"),
                          (64, "OFF"), (169, "OFF"), (411, "OFF")]),
        Spec("9039_1", D, dd, "232_2", sit=True, init_states=[(SHV, "OFF")],
             goal_states=[(1000, "ON"), (1001, "ON"), (169, "OFF"),
                          (TV, "ON"), (SHV, "PLUGGED_IN")]),
        Spec("9040_1", D, dd, "232_2", sit=True, init_states=[(SHV, "OFF")],
             goal_states=[(CLK, "OFF"), (SHV, "PLUGGED_IN"), (SHV, "ON"),
                          (245, "ON"), (411, "OFF"), (TV, "ON")]),
    ]

    # ── Family E: Rough morning (base 286_2) ────────────────────────────────
    E = "Hard rough morning"
    ed = "Wake up, free your hands, fetch groceries from closed containers into the freezer, and set devices."
    GLS, FD1, FD2, FD3 = 1000, 2009, 2013, 2016
    BOX, CON, PHN, DNT, PCL = 2014, 2010, 2015, 2007, 2008
    S += [
        Spec("9041_1", E, ed, "286_2", sit=True,
             stash=[(FD1, BOX), (FD2, BOX), (GLS, CUP)],
             goals_in=[(FD1, FRZ), (FD2, FRZ)],
             goals_on=[(GLS, T)],
             goal_states=[(BOX, "CLOSED"), (CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9042_1", E, ed, "286_2", sit=True, held=[(GLS, "RH")],
             stash=[(DNT, CUP)],
             goals_on=[(GLS, T), (DNT, T)],
             goal_states=[(CUP, "CLOSED"), (CON, "ON")]),
        Spec("9043_1", E, ed, "286_2",
             stash=[(FD1, CUP), (FD2, CUP), (FD3, CUP)],
             goals_in=[(FD1, FRZ), (FD2, FRZ), (FD3, FRZ)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED"), (PHN, "OFF")]),
        Spec("9044_1", E, ed, "286_2", sit=True,
             stash=[(FD3, BOX), (DNT, CUP)],
             goals_in=[(FD3, FRZ), (DNT, FRZ)],
             goal_states=[(BOX, "CLOSED"), (CUP, "CLOSED"), (FRZ, "CLOSED"), (CON, "ON")]),
        Spec("9045_1", E, ed, "286_2", held=[(GLS, "RH")],
             stash=[(FD1, BOX), (FD2, BOX)],
             goals_in=[(FD1, FRZ), (FD2, FRZ)],
             goals_on=[(GLS, T)],
             goal_states=[(BOX, "CLOSED"), (FRZ, "CLOSED"), (PHN, "OFF")]),
        Spec("9046_1", E, ed, "286_2", sit=True,
             stash=[(FD1, BOX), (FD2, BOX), (GLS, CUP)],
             goals_in=[(FD1, FRZ), (FD2, FRZ)],
             goals_on=[(GLS, T)],
             goal_states=[(BOX, "CLOSED"), (CUP, "CLOSED"), (FRZ, "CLOSED"),
                          (CON, "ON"), (PHN, "OFF")]),
        Spec("9047_1", E, ed, "286_2", sit=True,
             stash=[(DNT, CUP), (FD3, CUP)],
             goals_on=[(DNT, T), (FD3, T)],
             goal_states=[(CUP, "CLOSED"), (CON, "ON")]),
        Spec("9048_1", E, ed, "286_2", sit=True,
             stash=[(FD1, BOX), (FD2, BOX), (FD3, BOX)],
             goals_in=[(FD1, FRZ), (FD2, FRZ), (FD3, FRZ)],
             goal_states=[(BOX, "CLOSED"), (FRZ, "CLOSED"), (CON, "ON"), (PHN, "OFF")]),
        Spec("9049_1", E, ed, "286_2", held=[(GLS, "RH"), (PCL, "LH")],
             stash=[(FD1, CUP)],
             goals_in=[(FD1, FRZ)],
             goals_on=[(GLS, T), (PCL, NS)],
             goal_states=[(CUP, "CLOSED"), (FRZ, "CLOSED")]),
        Spec("9050_1", E, ed, "286_2", sit=True, held=[(GLS, "RH")],
             stash=[(FD1, BOX), (FD2, BOX), (FD3, BOX)],
             goals_in=[(FD1, FRZ), (FD2, FRZ), (FD3, FRZ)],
             goals_on=[(GLS, T)],
             goal_states=[(BOX, "CLOSED"), (FRZ, "CLOSED"), (CON, "ON"), (PHN, "OFF")]),
    ]
    return S


# ─────────────────────────────────────────────────────────────────────────────
# Generation + validation
# ─────────────────────────────────────────────────────────────────────────────

def write_task(spec):
    init, final = build_graphs(spec)
    goals = build_goals(spec, init)
    gold = build_gold(spec, init)

    with open(os.path.join(GRAPH_DIR, f"file{spec.tid}.json"), "w") as f:
        json.dump({"init_graph": init, "final_graph": final}, f)
    with open(os.path.join(SCRIPT_DIR, f"file{spec.tid}.txt"), "w") as f:
        f.write(spec.name + "\n" + spec.desc + "\n\n\n")
        f.write("\n".join(gold) + "\n")
    return goals, gold


def validate(spec, goals):
    import virtualhome_eval.simulation.evolving_graph.utils as utils
    from virtualhome_eval.simulation.evolving_graph.eval_utils import construct_planner
    props = utils.load_properties_data()
    placing = utils.load_object_placing()
    name_eq = utils.load_name_equivalence()
    planner, _, gd_actions, _, _ = construct_planner(
        name_eq, props, placing, scenegraph_id=1, script_id=spec.tid,
        dataset_root=DATASET)
    for i, a in enumerate(gd_actions):
        ok, info = planner.my_execute_primitive_action_eval(a)
        if not ok:
            return False, f"step {i+1} failed: {a}"
    env = planner.env_state.to_dict()
    nodes = {n["id"]: n for n in env["nodes"]}
    for g in goals:
        if "state" in g:
            if g["state"] not in nodes[g["id"]]["states"]:
                return False, f"node goal unmet: {g} (has {nodes[g['id']]['states']})"
        else:
            if g not in env["edges"]:
                return False, f"edge goal unmet: {g}"
    return True, f"{len(gd_actions)} gold steps, {len(goals)} goals"


def main():
    os.makedirs(OUT_RES, exist_ok=True)
    specs = make_specs()
    assert len(specs) == 50 and len({s.tid for s in specs}) == 50

    task_dict, id2task = {}, {}
    failures = []
    for spec in specs:
        goals, gold = write_task(spec)
        ok, msg = validate(spec, goals)
        status = "PASS" if ok else "FAIL"
        print(f"  {spec.tid}  {spec.name:28s} {status}  {msg}")
        if not ok:
            failures.append((spec.tid, msg))
        task_dict.setdefault(spec.name, {})[spec.tid] = {
            "vh_goal": {"goal": goals, "actions": []}}
        id2task[spec.tid] = spec.name

    with open(os.path.join(OUT_RES, "task_state_LTL_formula_accurate.json"), "w") as f:
        json.dump({"scene_1": task_dict}, f, indent=2)
    with open(os.path.join(OUT_RES, "id2task.json"), "w") as f:
        json.dump(id2task, f, indent=2)

    print(f"\n{50 - len(failures)}/50 tasks validated OK")
    if failures:
        print("FAILURES:")
        for tid, msg in failures:
            print(f"  {tid}: {msg}")
        sys.exit(1)
    print(f"task dict  → {OUT_RES}/task_state_LTL_formula_accurate.json")
    print(f"graphs     → {GRAPH_DIR}/file90xx_1.json")
    print(f"scripts    → {SCRIPT_DIR}/file90xx_1.txt")


if __name__ == "__main__":
    main()
