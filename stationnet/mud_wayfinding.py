"""Wayfinding: named destinations, what characters know of them, and routes there.

Builders mark rooms as destinations. A character may ask the way to one that
is public, that they have learned (by visiting it, from dialogue, a trigger or
a book), or whose map they carry, provided its conditions hold. Routes follow
the real room graph with the same exit rules as movement; terrain only makes
some ground slower, so a road is preferred to rough country.

The world is read lazily in square chunks and cached per navigation revision,
which SQL triggers bump on any room, exit or preset change, so a search never
loads a whole million-room wilderness and never outlives an edit. Directions
are told the way a traveller would give them: a general heading, the ground or
road to follow, known landmarks and the next major turn, never a step list.

This module reads the database but knows nothing of sessions or events;
mud.py supplies per-player exit checks and turns the results into messages.
"""
from collections import OrderedDict, namedtuple
import heapq
import itertools
import json
import math
import threading

from . import mud_scope
from .mud_wilderness import DELTAS, DIAGONALS, grid_linked, preset_for

CATEGORIES = ("town", "city", "settlement", "landmark", "dungeon", "road", "other")
CATEGORY_LABELS = {"town": "Towns", "city": "Cities", "settlement": "Settlements", "landmark": "Landmarks",
                   "dungeon": "Dungeons", "road": "Roads", "other": "Other places"}
MAX_ALIASES = 10
MAX_MAP_ITEMS = 10
MAX_TEACHES = 20
# What one room of each terrain costs a route; rooms are rooms, so this only
# steers routes toward roads and around rough ground, it never forbids any.
TERRAIN_COSTS = {"road": 2, "grassland": 3, "custom": 3, "forest": 4, "hills": 4, "desert": 4,
                 "tundra": 4, "dense-forest": 5, "swamp": 5, "mountains": 6, "water": 8}
AUTHORED_COST = 3
MIN_COST = min(TERRAIN_COSTS.values())
# A first, exact search finds the best route for ordinary distances; if it
# examines too many rooms, a greedier one (estimates weighted above 1) settles
# for a good route across very large country instead.
EXACT_EXPANSIONS = 40_000
GREEDY_WEIGHT = 2.5
MAX_EXPANSIONS = 80_000
CHUNK_SIZE = 32
MAX_CHUNKS = 512
MAX_LEGS = 4
COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")
ROOM_COLUMNS = "id,kind,name,x,y,z,preset_id,region_id,grid_link,blocked_dirs"

Node = namedtuple("Node", "id kind name x y z preset_id region_id grid_link blocked")
Exit = namedtuple("Exit", "id from_id to_id name direction key_item_id pick_difficulty conditions_json hidden")
Step = namedtuple("Step", "direction exit_name node")
Route = namedtuple("Route", "status start steps graph")
Leg = namedtuple("Leg", "kind steps start heading ground region landmarks")

_GRAPHS = {}
_ROUTES = {}
_LOCK = threading.RLock()


class WayfindingError(ValueError):
    pass


# --- Destinations and knowledge ---------------------------------------------

def _norm(text):
    return " ".join(str(text).casefold().split())


def aliases(row):
    return [part.strip() for part in (row["aliases"] or "").split(",") if part.strip()]


def map_items(row):
    try:
        value = json.loads(row["map_items_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _id_list(value, label, limit):
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            raise WayfindingError(f"{label} must be a JSON list of IDs.") from None
    if (not isinstance(value, list) or len(value) > limit
            or any(not isinstance(item, str) or not 1 <= len(item.strip()) <= 200 for item in value)):
        raise WayfindingError(f"{label} must be a list of at most {limit} IDs.")
    return list(dict.fromkeys(item.strip() for item in value))


def teaches(properties):
    """The destination IDs an item teaches when used or read; invalid lists teach nothing."""
    try:
        return _id_list(properties.get("teaches_destinations", []), "teaches_destinations", MAX_TEACHES)
    except WayfindingError:
        return []


def validate_teaches(value):
    return _id_list(value, "Item teaches_destinations", MAX_TEACHES)


def validate_destination(values):
    """Normalize destination fields from a builder form or a package row."""
    name = " ".join(str(values.get("name") or "").split())
    if not 2 <= len(name) <= 80:
        raise WayfindingError("Destination names must be 2 to 80 characters.")
    raw_aliases = values.get("aliases", "")
    parts = raw_aliases if isinstance(raw_aliases, list) else str(raw_aliases or "").split(",")
    names = [" ".join(str(part).split()) for part in parts if str(part).strip()]
    if len(names) > MAX_ALIASES or any(len(part) > 80 or "," in part for part in names):
        raise WayfindingError(f"Use at most {MAX_ALIASES} aliases of up to 80 characters, separated by commas.")
    category = values.get("category") or "landmark"
    if category not in CATEGORIES:
        raise WayfindingError(f"Destination category must be one of {', '.join(CATEGORIES)}.")
    description = " ".join(str(values.get("description") or "").split())
    if len(description) > 1000:
        raise WayfindingError("Destination descriptions cannot exceed 1000 characters.")
    flags = {}
    for key in ("public", "discover_on_visit"):
        value = values.get(key, 0)
        if value not in (0, 1, True, False):
            raise WayfindingError(f"Destination {key} must be true or false.")
        flags[key] = int(bool(value))
    order = values.get("sort_order", 0)
    if type(order) is not int or not -1000 <= order <= 1000:
        raise WayfindingError("Destination sort_order must be a whole number from -1000 to 1000.")
    return {"name": name, "aliases": ", ".join(dict.fromkeys(names)), "category": category,
            "description": description, **flags,
            "map_items": _id_list(values.get("map_items_json", []), "Destination map items", MAX_MAP_ITEMS),
            "sort_order": order}


def world_destinations(database, world_id):
    return database.execute(
        "SELECT d.*,r.name AS room_name,r.kind AS room_kind,r.x,r.y,r.z FROM mud_world_destinations AS d "
        "JOIN mud_world_rooms AS r ON r.id=d.room_id WHERE d.world_id=? "
        "ORDER BY d.sort_order,d.name COLLATE NOCASE", (world_id,)).fetchall()


def destination(database, destination_id):
    return database.execute("SELECT * FROM mud_world_destinations WHERE id=?", (destination_id,)).fetchone()


def known_ids(database, character_id):
    return {row["destination_id"] for row in database.execute(
        "SELECT destination_id FROM mud_character_destinations WHERE character_id=?", (character_id,))}


def knows(database, character_id, destination_id):
    return database.execute("SELECT 1 FROM mud_character_destinations WHERE character_id=? AND destination_id=?",
                            (character_id, destination_id)).fetchone() is not None


def learn(database, character_id, destination_id, source):
    """Remember a destination; returns its name when it was new to the character."""
    row = destination(database, destination_id)
    if row is None:
        return None
    added = database.execute("INSERT OR IGNORE INTO mud_character_destinations(character_id,destination_id,source) "
                             "VALUES(?,?,?)", (character_id, destination_id, source)).rowcount
    return row["name"] if added else None


def forget(database, character_id, destination_id):
    row = destination(database, destination_id)
    removed = database.execute("DELETE FROM mud_character_destinations WHERE character_id=? AND destination_id=?",
                               (character_id, destination_id)).rowcount
    return row["name"] if row is not None and removed else None


def discovered_here(database, room_id):
    return database.execute("SELECT id,name FROM mud_world_destinations WHERE room_id=? AND discover_on_visit=1 "
                            "ORDER BY sort_order,name COLLATE NOCASE", (room_id,)).fetchall()


def match(entries, text):
    """Destinations named by text: exact names or aliases first, then prefixes, then any part."""
    key = _norm(text)
    if not key:
        return []
    for test in (lambda name: name == key, lambda name: name.startswith(key), lambda name: key in name):
        found = [entry for entry in entries
                 if any(test(_norm(name)) for name in [entry["name"], *aliases(entry)])]
        if found:
            return found
    return []


def revision(database):
    row = database.execute("SELECT revision FROM mud_navigation_revision WHERE id=1").fetchone()
    return row[0] if row else 0


# --- The navigation graph ---------------------------------------------------

def _node(row):
    return Node(row["id"], row["kind"], row["name"], row["x"], row["y"], row["z"], row["preset_id"],
                row["region_id"], bool(row["grid_link"]),
                frozenset(part for part in (row["blocked_dirs"] or "").split(",") if part))


class _Graph:
    """One world's rooms as a route search sees them, valid for one navigation revision.

    Authored rooms and the ends of stored exits are loaded up front, since a
    world holds at most a few thousand; grid rooms load by chunk on demand.
    """

    def __init__(self, database, world_id, revision_number):
        self.world_id, self.revision, self.key = world_id, revision_number, None
        self.chunks = OrderedDict()
        self.nodes = {row["id"]: _node(row) for row in database.execute(
            f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE world_id=? AND kind='authored'", (world_id,))}
        self.exits, self.reverse = {}, {}
        for row in database.execute(
                "SELECT e.id,e.from_room_id,e.to_room_id,e.name,e.direction,e.key_item_id,e.pick_difficulty,"
                "e.conditions_json,e.hidden FROM mud_world_exits AS e "
                "JOIN mud_world_rooms AS r ON r.id=e.from_room_id WHERE r.world_id=? ORDER BY e.id", (world_id,)):
            item = Exit(row["id"], row["from_room_id"], row["to_room_id"], row["name"],
                        (row["direction"] or "").casefold() or None, row["key_item_id"], row["pick_difficulty"],
                        row["conditions_json"], bool(row["hidden"]))
            self.exits.setdefault(item.from_id, []).append(item)
            self.reverse.setdefault(item.to_id, []).append(item.from_id)
        missing = sorted({room for item in self.reverse for room in (item, *self.reverse[item])} - set(self.nodes))
        for start in range(0, len(missing), 500):
            part = missing[start:start + 500]
            for row in database.execute(f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE id IN "
                                        f"({','.join('?' * len(part))})", part):
                self.nodes[row["id"]] = _node(row)
        self.regions = {row["id"]: row["name"] for row in database.execute(
            "SELECT id,name FROM mud_world_regions WHERE world_id=?", (world_id,))}
        self.presets, self.anchors = {}, {}

    def node(self, database, room_id):
        found = self.nodes.get(room_id)
        if found is None:
            row = database.execute(f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE id=? AND world_id=?",
                                   (room_id, self.world_id)).fetchone()
            found = _node(row) if row is not None else None
        return found

    def at(self, database, x, y, z):
        key = (z, x // CHUNK_SIZE, y // CHUNK_SIZE)
        chunk = self.chunks.get(key)
        if chunk is None:
            left, top = key[1] * CHUNK_SIZE, key[2] * CHUNK_SIZE
            chunk = {(row["x"], row["y"]): _node(row) for row in database.execute(
                f"SELECT {ROOM_COLUMNS} FROM mud_world_rooms WHERE world_id=? AND z=? "
                "AND y BETWEEN ? AND ? AND x BETWEEN ? AND ?",
                (self.world_id, z, top, top + CHUNK_SIZE - 1, left, left + CHUNK_SIZE - 1))}
            self.chunks[key] = chunk
            if len(self.chunks) > MAX_CHUNKS:
                self.chunks.popitem(last=False)
        else:
            self.chunks.move_to_end(key)
        return chunk.get((x, y))

    def preset(self, database, preset_id):
        """(terrain, links, ground name) for a preset; unknown presets read as plain wilds."""
        if preset_id not in self.presets:
            preset = preset_for(database, preset_id)
            self.presets[preset_id] = ((preset["terrain"], preset["links"], preset["name"].casefold())
                                       if preset else ("custom", "4", "wilds"))
        return self.presets[preset_id]

    def links(self, database, node):
        return "8" if node.kind != "wild" else self.preset(database, node.preset_id)[1]

    def terrain(self, database, node):
        return None if node.kind != "wild" else self.preset(database, node.preset_id)[0]

    def cost(self, database, node):
        return AUTHORED_COST if node.kind != "wild" else TERRAIN_COSTS.get(self.terrain(database, node), 3)

    def position(self, node):
        """A node's coordinates, or for an off-grid room those of the nearest placed room."""
        if node.x is not None:
            return node.x, node.y, node.z
        if node.id not in self.anchors:
            found, queue, seen = None, [node.id], {node.id}
            for room_id in queue:
                other = self.nodes.get(room_id)
                if other is not None and other.x is not None:
                    found = (other.x, other.y, other.z)
                    break
                if len(seen) > 500:
                    break
                for neighbour in [item.to_id for item in self.exits.get(room_id, ())] + self.reverse.get(room_id, []):
                    if neighbour not in seen:
                        seen.add(neighbour)
                        queue.append(neighbour)
            self.anchors[node.id] = found
        return self.anchors[node.id]

    def neighbours(self, database, node, allowed):
        """Rooms one step away by the same rules as movement; allowed(exit) judges stored exits."""
        stored = self.exits.get(node.id, ())
        # Stored exits win their direction even while hidden or sealed, as in _room_exits.
        taken = {item.direction for item in stored if item.direction}
        for item in stored:
            if allowed(item):
                target = self.node(database, item.to_id)
                if target is not None:
                    yield Step(item.direction, item.name, target)
        if node.x is None or not node.grid_link:
            return
        own_links = self.links(database, node)
        for direction, (dx, dy, dz) in DELTAS.items():
            if direction in taken:
                continue
            other = self.at(database, node.x + dx, node.y + dy, node.z + dz)
            if other is not None and grid_linked(direction, node.blocked, own_links, other.grid_link, other.blocked,
                                                 self.links(database, other) if direction in DIAGONALS else ""):
                yield Step(direction, direction, other)


def _database_key(database):
    """Which database file a connection uses, so caches never cross databases."""
    row = database.execute("PRAGMA database_list").fetchone()
    return row[2] if row else ""


def _graph(database, world_id):
    current = revision(database)
    key = (_database_key(database), world_id)
    graph = _GRAPHS.get(key)
    if graph is None or graph.revision != current:
        if len(_GRAPHS) >= 2:
            _GRAPHS.clear()
        graph = _GRAPHS[key] = _Graph(database, world_id, current)
        graph.key = key
    return graph


def find_route(database, world_id, start_id, goal_id, allowed, budget=MAX_EXPANSIONS):
    """A* from one room to another: exact first, then greedier if the exact search runs long.

    status is 'ok' with the steps, 'none' when no route exists, or 'far' when
    the search gave up before finding one.
    """
    with _LOCK:
        graph = _graph(database, world_id)
        start, goal = graph.node(database, start_id), graph.node(database, goal_id)
        if start is None or goal is None:
            return Route("none", start, [], graph)
        if start.id == goal.id:
            return Route("ok", start, [], graph)
        route = _search(database, graph, start, goal, allowed, 1, min(budget, EXACT_EXPANSIONS))
        if route.status == "far" and budget > EXACT_EXPANSIONS:
            route = _search(database, graph, start, goal, allowed, GREEDY_WEIGHT, budget)
        return route


def _search(database, graph, start, goal, allowed, weight, budget):
    target = graph.position(goal)

    def estimate(node):
        here = graph.position(node)
        if here is None or target is None:
            return 0
        return weight * MIN_COST * (max(abs(here[0] - target[0]), abs(here[1] - target[1]))
                                    + abs(here[2] - target[2]))

    counter = itertools.count()
    costs, parents, closed = {start.id: 0}, {start.id: None}, set()
    heap = [(estimate(start), 0, next(counter), start)]
    while heap:
        _, negative, _, node = heapq.heappop(heap)
        if node.id in closed:
            continue
        if node.id == goal.id:
            steps, cursor = [], node.id
            while parents[cursor] is not None:
                cursor, step = parents[cursor]
                steps.append(step)
            return Route("ok", start, steps[::-1], graph)
        closed.add(node.id)
        if len(closed) > budget:
            return Route("far", start, [], graph)
        cost = -negative
        for step in graph.neighbours(database, node, allowed):
            following = step.node
            if following.id in closed:
                continue
            total = cost + graph.cost(database, following)
            if total < costs.get(following.id, math.inf):
                costs[following.id] = total
                parents[following.id] = (node.id, step)
                # Equal estimates favour the deeper room, which reaches the goal sooner.
                heapq.heappush(heap, (total + estimate(following), -total, next(counter), following))
    return Route("none", start, [], graph)


# --- Remembered routes ------------------------------------------------------

class _Tracked:
    def __init__(self, destination_id, route):
        self.destination_id, self.graph = destination_id, route.graph
        self.nodes = [route.start] + [step.node for step in route.steps]
        self.steps = list(route.steps)
        self.index = {node.id: position for position, node in enumerate(self.nodes)}
        self.last = 0

    def locate(self, room_id, position):
        """Where a room falls on the route; a room beside it still counts as on the way."""
        found = self.index.get(room_id)
        if found is None and position is not None:
            for spot in range(max(0, self.last - 2), min(len(self.nodes), self.last + 12)):
                node = self.nodes[spot]
                if (node.x is not None and node.z == position[2]
                        and max(abs(node.x - position[0]), abs(node.y - position[1])) <= 1):
                    found = spot
                    break
        if found is not None:
            self.last = found
        return found


def _route_key(user_id):
    # A player can follow a route in each world they have a character in.
    return user_id, mud_scope.get()


def remember(user_id, destination_id, route):
    with _LOCK:
        _ROUTES[_route_key(user_id)] = _Tracked(destination_id, route)
        return _ROUTES[_route_key(user_id)]


def recall(database, user_id, destination_id, world_id, *, stale=False):
    """A remembered route if it is still for this destination and this navigation revision.

    stale also accepts a route from before the last world edit, to compare against.
    """
    with _LOCK:
        tracked = _ROUTES.get(_route_key(user_id))
        if (tracked is None or tracked.destination_id != destination_id
                or tracked.graph.key != (_database_key(database), world_id)
                or not stale and tracked.graph.revision != revision(database)):
            return None
        return tracked


def drop_route(user_id):
    with _LOCK:
        _ROUTES.pop(_route_key(user_id), None)


# --- Telling the way --------------------------------------------------------

def sector(dx, dy):
    """The compass point index (0 = north) of a grid vector; y grows southward."""
    if dx == 0 and dy == 0:
        return None
    return round(math.degrees(math.atan2(dx, -dy)) / 45) % 8


def sector_gap(first, second):
    gap = abs(first - second) % 8
    return min(gap, 8 - gap)


def bearing(origin, target):
    if origin is None or target is None or origin[2] != target[2]:
        return None
    index = sector(target[0] - origin[0], target[1] - origin[1])
    return COMPASS[index] if index is not None else None


def turns_from(direction, heading):
    """True when walking direction would leave a route heading by more than one compass point."""
    if not heading or not direction or heading == direction:
        return False
    if heading in COMPASS and direction in COMPASS:
        return sector_gap(COMPASS.index(heading), COMPASS.index(direction)) >= 2
    return True


def _distance(steps):
    if steps <= 3:
        return "a few paces"
    if steps <= 10:
        return "a short way"
    if steps <= 30:
        return "some distance"
    if steps <= 100:
        return "a long way"
    return "a great distance"


def _journey(steps):
    if steps <= 3:
        return "close by"
    if steps <= 10:
        return "a short walk away"
    if steps <= 30:
        return "a fair walk away"
    if steps <= 100:
        return "a long journey away"
    return "a great journey away"


def _kind(graph, database, step):
    if step.direction in ("up", "down"):
        return "vertical"
    if step.node.kind != "wild":
        return "built"
    return "road" if graph.terrain(database, step.node) == "road" else "wild"


def _point(node):
    return (node.x, node.y) if node.x is not None else None


def _split_by_heading(start, steps):
    """Break a run of open-ground steps where its general heading swings more than 45 degrees."""
    points = [_point(start)] + [_point(step.node) for step in steps]
    pieces, begin, index = [], 0, 1
    while index <= len(steps):
        if index - begin >= 4 and all(points[spot] is not None for spot in (begin, index - 3, index)):
            base = sector(points[index - 3][0] - points[begin][0], points[index - 3][1] - points[begin][1])
            recent = sector(points[index][0] - points[index - 3][0], points[index][1] - points[index - 3][1])
            if base is not None and recent is not None and sector_gap(base, recent) >= 2:
                pieces.append((begin, index - 3))
                begin = index - 3
                index = begin + 1
                continue
        index += 1
    pieces.append((begin, len(steps)))
    return pieces


def _heading(start, steps):
    first, last = _point(start), _point(steps[-1].node)
    if first is not None and last is not None:
        index = sector(last[0] - first[0], last[1] - first[1])
        if index is not None:
            return COMPASS[index]
    directions = [step.direction for step in steps if step.direction]
    return max(set(directions), key=directions.count) if directions else None


def legs(graph, database, start, steps, landmarks=None):
    """Group a route into legs a traveller would describe: roads, open ground, built places, climbs."""
    landmarks = landmarks or {}
    groups, previous = [], start
    for position, step in enumerate(steps):
        kind = _kind(graph, database, step)
        key = (kind, step.node.preset_id if kind == "wild" else None,
               step.node.region_id if kind in ("wild", "road") else None)
        if groups and groups[-1][0] == key and kind != "vertical":
            groups[-1][2].append(step)
        else:
            groups.append((key, previous, [step]))
        previous = step.node
    result = []
    for (kind, _preset, region_id), group_start, group in groups:
        pieces = _split_by_heading(group_start, group) if kind in ("wild", "road") else [(0, len(group))]
        for begin, end in pieces:
            chunk = group[begin:end]
            if not chunk:
                continue
            first = group_start if begin == 0 else group[begin - 1].node
            ground = graph.preset(database, chunk[0].node.preset_id)[2] if kind == "wild" else None
            passed = [landmarks[step.node.id] for step in chunk[:-1] if step.node.id in landmarks]
            result.append(Leg(kind, chunk, first, _heading(first, chunk), ground,
                              graph.regions.get(region_id) if region_id else None, list(dict.fromkeys(passed))))
    return result


def _move(step):
    if step.direction and step.direction == step.exit_name:
        return f"go {step.direction}"
    return f"take the {step.exit_name}" + (f" {step.direction}" if step.direction else "")


def _leg_text(leg, following, destination_name, final=True):
    count = len(leg.steps)
    last = leg.steps[-1].node
    if leg.kind == "road":
        text = f"follow the road {leg.heading} {_distance(count)}"
        if leg.region:
            text += f" through {leg.region}"
    elif leg.kind == "wild":
        text = f"head {leg.heading} across the {leg.ground}"
        if leg.region:
            text += f" of {leg.region}"
        text += f" {_distance(count)}"
    elif leg.kind == "vertical":
        text = ("climb up" if leg.steps[0].direction == "up" else "go down")
        if last.kind != "wild" and last.name:
            text += f" to {last.name}"
    else:
        moves = [f"{_move(step)} to {step.node.name}" for step in leg.steps]
        if len(moves) > 3:
            moves = moves[:2] + [f"make your way through {len(moves) - 2} more rooms to {last.name}"]
        text = ", then ".join(moves)
    if leg.landmarks:
        text += ", past " + " and ".join(leg.landmarks)
    if following is not None:
        if following.kind == "road" and leg.kind != "road":
            text += " until you meet a road"
        elif following.kind == "wild" and (leg.kind != "wild" or following.ground != leg.ground):
            text += f" until you reach the {following.ground}"
    elif not final:
        pass
    elif leg.kind in ("road", "wild"):
        text += f" to reach {destination_name}"
    elif last.name != destination_name:
        text += f", where you will find {destination_name}"
    return text


def _capital(text):
    return text[:1].upper() + text[1:]


def describe(graph, database, start, steps, destination_name, landmarks=None):
    """Directions as a few sentences: a summary, then up to MAX_LEGS legs."""
    route_legs = legs(graph, database, start, steps, landmarks)
    target = graph.position(steps[-1].node) if steps else None
    direction = bearing(graph.position(start), target)
    lines = [f"{destination_name} is {_journey(len(steps))}" + (f" to the {direction}." if direction else ".")]
    for index, leg in enumerate(route_legs[:MAX_LEGS]):
        following = route_legs[index + 1] if index + 1 < len(route_legs) else None
        text = _leg_text(leg, following, destination_name)
        prefix = "" if index == 0 else ("finally, " if following is None and index > 1 else "then ")
        lines.append(_capital(prefix + text) + ".")
    if len(route_legs) > MAX_LEGS:
        lines.append("Beyond that, ask again once you are closer.")
    return lines


def _general(leg):
    return leg.heading if leg.kind in ("wild", "road") else leg.steps[0].direction


def heading(graph, database, start, steps, destination_name, landmarks=None):
    """The next leg only, as (direction to walk now, sentence) for badges and travel stops.

    When a leg begins by cutting a corner, the direction is that first step, so
    /travel that way does not leave the road.
    """
    route_legs = legs(graph, database, start, steps[:400], landmarks)
    if not route_legs:
        return None, ""
    first = route_legs[0]
    following = route_legs[1] if len(route_legs) > 1 else None
    text = _leg_text(first, following, destination_name, len(steps) <= 400)
    direction = _general(first)
    step = first.steps[0].direction
    if (first.kind in ("wild", "road") and step in COMPASS and direction in COMPASS and step != direction
            and (len(first.steps) == 1 or first.steps[1].direction == direction)):
        return step, _capital(f"go {step}, then {text}") + "."
    return direction, _capital(text) + "."


def turn_ahead(graph, database, nodes, steps, spot, direction):
    """The heading to announce when walking on in direction would leave the route here, else None."""
    route_legs = legs(graph, database, nodes[spot], steps[spot:spot + 400])
    if not route_legs:
        return None
    general = _general(route_legs[0])
    if turns_from(direction, general):
        return general
    if general == direction or steps[spot].direction == direction:
        return None
    # Close to the route's heading but about to step off it, as at a cut corner.
    here, delta = nodes[spot], DELTAS.get(direction)
    if delta and here.x is not None:
        ahead = (here.x + delta[0], here.y + delta[1], here.z + delta[2])
        if any((node.x, node.y, node.z) == ahead for node in nodes[spot + 1:spot + 3]):
            return None
    return general
