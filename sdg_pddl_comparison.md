# State Dependency Graph vs the VirtualHome specification

This file documents what  SDG requires for each of its 42 actions, how that relates to VirtualHome's own formal specification, and why it departs from that specification in 14 places.

The short version: SDG  models what the VirtualHome simulator *actually enforces at runtime*, not what the specification says it should. Where the two disagree, SDG  follows the simulator, and every such case is listed below with the executor code that justifies it.

---

## 1. The three files involved

Understanding anything below depends on keeping these three straight.

**`virtualhome.pddl`** — the *specification*. A formal planning-language document stating, action by action, what must be true before an action runs and what becomes true afterward. It reads like an authority, and was written to be one.

**`execution.py`** — the *simulator*. The Python code that runs a plan and decides, step by step, whether each action succeeds. When a plan is scored, this is the code doing the judging.

**`sdg.py`** — *our model*. A dictionary restating each action's requirements as a flat list of named conditions:

```python
"GRAB": {"needs": ["next_to_obj", "grabbable", "not_both_hands_full", ...]}
```

The planner uses it to check a plan before running it and — when a step fails — to determine *which* requirement was unmet, so the repair loop knows what to fix.

### Which of these are ours

| File | Origin | Modified by us |
|---|---|---|
| `virtualhome.pddl` | Benchmark | **No** — byte-identical to upstream |
| `execution.py` | Benchmark | **No** — byte-identical to upstream |
| `sdg.py` | **Ours** — original SDA-Planner code, no upstream counterpart | Yes |
| `object_state_model.py` | **Ours** — original SDA-Planner code, no upstream counterpart | Yes |

Neither the simulator nor the specification is altered. `sdg.py` and `object_state_model.py` are original planner-side code written for this project; neither exists anywhere in the benchmark's source tree. They govern how *our planner reasons*, never how the benchmark evaluates.

---

## 2. Why the simulator is the authority, not the specification

`sdg.py` was built by working through the PDDL action by action. That was the right starting point, but the PDDL does not reliably describe what the simulator does.

Sometimes the PDDL states a requirement `execution.py` never implements. Sometimes `execution.py` enforces something the PDDL never mentions. Occasionally they contradict each other outright.

A plan lives or dies by what `execution.py` does. So `sdg.py`'s target is `execution.py`, and the PDDL is where the porting started — not the standard it is held to.

---

## 3. Why an inaccurate model is costly, and why direction decides how costly

Not all inaccuracies are equal. Which way the error points changes everything.

**Too strict** — `sdg.py` demands something the simulator doesn't care about. The plan would have worked, but `sdg.py` reports an unmet requirement anyway and the repair loop inserts a corrective action nobody needed. This wastes repair budget and can crowd out useful actions, but the plan still runs.

**Too permissive** — `sdg.py` misses something the simulator enforces. This is the damaging one. The action fails during execution, the diagnosis asks `sdg.py` which requirement was unmet, and the answer comes back *"none — everything was satisfied."* The repair loop now has a failure it cannot explain and nothing to act on. It goes blind.

`sdg.py`'s own comment on OPEN names this failure directly: *"an OPEN-with-both-hands-full failure diagnoses as Unsat=[] and the repair loop goes blind."*

That asymmetry is why `sdg.py` tracks the executor precisely rather than approximately, and why the departures in section 4 are deliberate rather than sloppy.

---

## 4. How the 42 actions relate to the PDDL

The PDDL defines 33 actions, two of which (`walk_towards`, `walk_into`) both map to the single script action WALK — so it covers 32 of `sdg.py`'s 42. The other 10 have no PDDL counterpart.

| Category | Count | Meaning |
|---|---|---|
| **Match** | 22 | Faithful, complete transcription of the PDDL. |
| **Match + extra** | 6 | Keeps the whole PDDL requirement, plus something the PDDL never mentions but the simulator enforces. |
| **Override** | 4 | Deliberately **discards or replaces** something the PDDL states, because the simulator doesn't implement it. |
| **Alias** | 6 | No PDDL entry by this name; requirements taken from a closely related PDDL action. |
| **Original** | 4 | No PDDL entry and no PDDL relative by name; requirements come from the simulator alone. |

### Match (22) — straight transcriptions

| Action | PDDL action | PDDL precondition | sdg.py needs |
|---|---|---|---|
| WALK | `walk_towards` / `walk_into` | not sitting, not lying | `not_sitting`, `not_lying` |
| TURNTO | `turn_to` | none | (none) |
| GRAB | `grab` | grabbable, next_to, not inside a closed container, not both hands full | `next_to_obj`, `grabbable`, `not_both_hands_full`, `obj_not_inside_closed_container` |
| PUTBACK | `put_on` | next_to target, holding the object | `next_to_target`, `holds_obj` |
| PUTIN | `put_inside` | next_to target, holding the object, and (target not openable OR target is open) | `next_to_target`, `holds_obj`, `target_open_or_not_openable` |
| POUR | `pour` | source is pourable or drinkable, holding source, target is a recipient, next_to target | `holds_obj`, `pourable_or_drinkable`, `next_to_target`, `target_is_recipient` |
| CLOSE | `close` | can_open, currently open, next_to | `next_to_obj`, `can_open`, `open` |
| SWITCHON | `switch_on` | has_switch, currently off, plugged in, next_to | `next_to_obj`, `has_switch`, `off`, `plugged_in` |
| SWITCHOFF | `switch_off` | has_switch, currently on, next_to | `next_to_obj`, `has_switch`, `on` |
| SIT | `sit` | next_to, sittable, not already sitting | `next_to_obj`, `sittable`, `not_sitting` |
| STANDUP | `standup` | sitting or lying | `sitting_or_lying` |
| LIE | `lie` | lieable, next_to, not already lying | `next_to_obj`, `lieable`, `not_lying` |
| SLEEP | `sleep` | sitting or lying | `sitting_or_lying` |
| WAKEUP | `wake_up` | sitting or lying | `sitting_or_lying` |
| WASH | `wash` | next_to | `next_to_obj` |
| WIPE | `wipe` | next_to the surface, holding a second, unconstrained object | `next_to_obj`, `holding_anything` |
| DRINK | `drink` | holding the object, and (drinkable OR recipient) | `holds_obj`, `drinkable_or_recipient` |
| EAT | `eat` | next_to, eatable | `next_to_obj`, `eatable` |
| READ | `read` | readable, holding the object | `holds_obj`, `readable` |
| WATCH | `watch` | lookable, facing, not inside a closed container | `lookable`, `facing_obj`, `obj_not_inside_closed_container` |
| LOOKAT | `look_at` | facing | `facing_obj` |
| TYPE | `type` | has_switch, next_to | `next_to_obj`, `has_switch` |

### Match + extra (6) — the PDDL requirement, plus one the simulator adds

Four of the additions are a free-hand check; two are property checks. The PDDL's own conditions are kept in full in every case.

| Action | PDDL precondition | sdg.py needs | Added beyond PDDL, and why |
|---|---|---|---|
| OPEN | can_open, closed, next_to, not on | `next_to_obj`, `can_open`, `closed`, `not_on`, **`not_both_hands_full`** | `OpenExecutor.check_openable` calls `_find_free_hand`. OPEN-specific: CLOSE shares the same executor class, but the call sits behind `if not self.close`, so CLOSE genuinely doesn't need it and its entry stays PDDL-faithful. |
| MOVE | movable, next_to, not inside a closed container | `next_to_obj`, `movable`, `obj_not_inside_closed_container`, **`not_both_hands_full`** | `MoveExecutor.check_movable` calls `_find_free_hand`. |
| PLUGOUT | next_to, has_plug, plugged in, not on | `next_to_obj`, `has_plug`, `plugged_in`, `not_on`, **`not_both_hands_full`** | `PlugExecutor.check_plugable` calls `_find_free_hand`, ungated by plug direction. |
| SQUEEZE | next_to, clothes | `next_to_obj`, `clothes`, **`not_both_hands_full`** | `SqueezeExecutor.check_squeezable` calls `_find_free_hand`. |
| CUT | next_to, eatable, cuttable | `next_to_obj`, `eatable`, `cuttable`, **`not_both_hands_full`** | `CutExecutor.check_cuttable` calls `_find_free_hand`. It also requires holding a knife — see section 6. |
| PUTON | holding the object | `holds_obj`, **`clothes`** | `PutOnExecutor.check_puton` requires Property.CLOTHES; the PDDL's `put_on_character` never mentions it. Without this, PUTON on a non-clothes object fails in the simulator with an empty diagnosis — the blind case from section 3. |

### Override (4) — where sdg.py contradicts the PDDL on purpose

These four are what this document is really about: the PDDL says one thing, `execution.py` does another, and `sdg.py` follows the code.

**FIND.** The PDDL requires `next_to` and produces no effect. Read literally that's circular — FIND requires already standing next to the thing you're trying to find, and achieves nothing. Unusable. `FindExecutor` in fact auto-navigates: if you're not close, it walks you there first, exactly like WALK. So `sdg.py` gives FIND no precondition and lists `next_to_obj` as its *effect* instead.

**PLUGIN.** The one case where the two disagree about which *property* is required. The PDDL's `plug_in` is an OR — an object qualifies either by having a plug *or* by having a switch. `sdg.py` requires `has_plug` alone, because `PlugExecutor.check_plugable` checks `HAS_PLUG` unconditionally and contains no `has_switch` branch anywhere, for plugging in or out. Whoever wrote the PDDL modeled a switch-based path that was never implemented, so here the specification is simply wrong about the simulator.

**DROP.** The PDDL requires holding the object **and** `obj_inside(?obj, ?room)` — the object must be in the current room. `DropExecutor.check_drop` only ever checks holding; no room condition exists in the class, so `sdg.py` requires `holds_obj` alone. This keeps DROP consistent with RELEASE, which shares the same executor and is modeled identically.

**TOUCH.** The largest single divergence in the file. The PDDL requires `readable`, holding the object, and not-inside-a-closed-container. `TouchExecutor.check_reachable` checks *neither* readable nor holding — only `_is_character_close_to` (proximity) and `_is_inside` (closed container). So `sdg.py` requires proximity, which the PDDL omits, and drops two of the PDDL's three conditions. Worth contrasting with READ, whose `readable` + holding requirement genuinely *is* enforced by `ReadExecutor` — the two actions look parallel but aren't.

### Alias (6) — inherited from a related action

The PDDL doesn't define these; each takes the requirements of the action it behaves like.

| Action | Behaves like | sdg.py needs | Note |
|---|---|---|---|
| RUN | `walk_towards` / `walk_into` | `not_sitting`, `not_lying` | Identical to WALK. |
| RINSE | `wash` | `next_to_obj` | Identical to WASH. |
| SCRUB | `wash` | `next_to_obj` | Identical to WASH. |
| PULL | `move` | `next_to_obj`, `movable`, `obj_not_inside_closed_container`, `not_both_hands_full` | Identical to MOVE, free-hand check included. |
| PUSH | `move` | `next_to_obj`, `obj_not_inside_closed_container`, `not_both_hands_full` | Same as MOVE **except** no `movable`: `MoveExecutor.check_movable` gates that check behind `action_name != "push"`, exempting PUSH alone. The free-hand check is *not* gated, so PUSH keeps it. |
| RELEASE | `drop` | `holds_obj` | Identical to DROP — both dispatch to `DropExecutor`, which checks only holding. |

### Original (4) — no PDDL basis at all

| Action | sdg.py needs | Where the requirements come from |
|---|---|---|
| POINTAT | `facing_obj` | No `point_at` action exists in the PDDL. `PointAtExecutor` is a literal Python alias of `LookAtExecutor` (`execution.py:2270`), so POINTAT's real requirement is facing — discoverable only by reading the source, not from the PDDL or the action's name. |
| PUTOBJBACK | `holds_obj` | No PDDL action. `PutBackExecutor` re-checks the object's remembered pickup location, which `sdg.py` doesn't track — so this one-line entry is a deliberate simplification, not a full model. |
| PUTOFF | `on_char`, `clothes` | No PDDL action — the PDDL has `put_on_character` with no reverse. Both requirements come from `PutOffExecutor.check_putoff`, which checks the object is currently worn **and** has Property.CLOTHES. |
| GREET | `person` | No PDDL action. `GreetExecutor` checks Property.PERSON and nothing else — notably *not* proximity, so GREET requires no WALK. |

---

## 5. How sdg.py's conditions are evaluated

`sdg.py` only stores condition *names*. Something has to answer, against a concrete scene, *"is `apple_7` grabbable? is the character next to it?"* That is `object_state_model.py`, whose `satisfies()` method matches on the condition name.

Two properties of that method matter for anyone extending `sdg.py`:

**Unrecognised conditions return `True`.** This is a deliberate "don't hard-block on an unknown condition" fallback. The consequence is that adding a new condition name to `sdg.py` alone does nothing — it will silently pass on every object, which looks like a working requirement while enforcing nothing. Any new condition must be added in both places.

`person` is the clearest example. It is evaluated by:

```python
if precondition == "person":
    return self.has_state(obj, "PERSON")
```

PERSON is a genuine VirtualHome property, carried by the classes `man`, `woman`, and `child`.

**Everything is keyed per instance.** State is stored under `class_name_id` tokens (`light_245`), not class names. An earlier design merged by class, so `light_245` being OFF and `light_246` being ON merged into `{ON, OFF}` and *both* the "on" and "off" checks passed. Per-instance keying is what makes goal-specific reasoning possible, and is the same discipline behind `one_shot.py`'s Rule 4.

---

## 6. What is deliberately not modeled

**CUT's knife requirement.** `CutExecutor.check_cuttable` also requires holding an object whose `class_name` contains `"knife"`. This can't be written as a flat condition name: it needs a query over the *class names of currently held objects*, a different shape than anything `sdg.py`'s schema expresses. It's stated in the prompt layer instead (`one_shot.py` Rule 9, `SYSTEM_PROMPT` RULE 8), so the model is told up front even though the repair loop can't diagnose it.

**Four structural checks**, out of scope for the same reason — each depends on state or logic outside a per-object condition list:

- WATCH's same-room check
- SIT and LIE's per-class occupancy caps (chair=1, couch=4, bed=3)
- WALK's closed-door path-blocking
- PUTOBJBACK's dependence on a remembered pickup location

### Two checks that exist in the executor but correctly aren't modeled

`execution.py` contains two precondition checks that `sdg.py` does not implement. Both are **commented out** upstream and never run:

- `SwitchExecutor.check_switchable` — a `_find_free_hand` check. SWITCHON and SWITCHOFF therefore have no free-hand requirement.
- `WipeExecutor.check_wipe` — a `Property.SURFACES` check on the wiped object. WIPE therefore accepts any target.

`sdg.py` models the code that executes, not commented-out intent. Noted here so a future audit doesn't raise them as gaps — and because if either is re-enabled upstream, `sdg.py` would need `not_both_hands_full` on the two switch actions and a new `surfaces` condition for WIPE.

---

## 7. Basis for the claims in this document

Every claim above was checked against the sources directly:

- **Every needs-list** compared programmatically against `sdg.py`'s live values: 42/42 exact.
- **Every action** mapped to its Executor class through `execution.py`'s dispatch table: exact 42↔42 correspondence, nothing missing in either direction.
- **Property checks in both directions**, with commented-out code stripped first: no property the simulator enforces is missing from `sdg.py`, and no property `sdg.py` demands goes unchecked by the simulator.
- **Free-hand checks** aligned across all 42 — nine actions require one (GRAB, OPEN, MOVE, PUSH, PULL, SQUEEZE, PLUGIN, PLUGOUT, CUT), matching the executor exactly. The one apparent exception, CLOSE, is confirmed as the `if not self.close` gate inside the shared `OpenExecutor`.
- **Internal completeness**: every condition used anywhere in `SDG` has both a human-readable explanation in `PRECONDITION_EXPLANATIONS` and an evaluable branch in `object_state_model.satisfies()`.
- **`sdg.py`'s self-test**: 33 checks, all passing.

---

## 8. Summary

Of 42 actions: **22** are faithful transcriptions of the PDDL. **6** keep the PDDL requirement and add something it never specified. **4** deliberately discard or replace a PDDL condition the simulator doesn't implement. **6** have no PDDL definition and inherit from a relative. **4** have no PDDL basis at all.

So `sdg.py` is not a port of the PDDL. It started as one, but 14 of its 42 entries differ, and every difference traces to a specific place where the specification and the running simulator disagree.

The accurate description: **a model of `execution.py`, built from the PDDL and corrected against the code wherever the two conflict.**
