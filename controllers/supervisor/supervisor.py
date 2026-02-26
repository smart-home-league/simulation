"""
Description:
This supervisor tracks down the absolute position of the robot
and removes the dust from the area covered by the robot.
It also exposes a small local web dashboard where you can:
- upload a team name and code file
- watch the score update in real time
"""

import json
import math
from dataclasses import dataclass
from typing import List, Literal, Optional, Union, Tuple
from controller import Node, Supervisor as BaseSupervisor
from web_dashboard import (
    start_server,
    update_score,
    set_team_name,
    set_room_stats,
    set_battery,
    set_subleague,
    consume_new_code_flag,
    consume_run_request,
    consume_relocate_request,
    consume_end_request,
)
import helpers

X = 0
Y = 1
Z = 2

@dataclass
class SupervisorConfig:
    """The configuration for the supervisor."""

    time_step: int = 16
    """The time step for the simulation in milliseconds."""
    robot_translation: Tuple[float, float, float] = (1.8761, -6.3738, 0.0442)
    """The translation of the robot."""
    subleague: Optional[Union[Literal["U14"], Literal["U19"], Literal["FS"]]] = None
    """The subleague of the competition."""
    ground_size: Optional[Tuple[int, int]] = None
    """The size of the ground in pixels."""
    ground_cell_size: int = 5
    """The size of the cell in pixels."""
    ground_clean_radius: int = 5
    """The radius of the cleaned spot in pixels."""
    run_time_limit: float = 60 * 6
    """The time limit for the run in seconds."""
    battery_drain_rate: float = 1.0
    """The rate of the battery drain in percent per second."""
    battery_charge_radius: float = 0.3
    """The radius of the battery charge in meters."""
    boost_radius: float = 0.35
    """U14: radius to trigger boost pad (+200 pts), meters."""
    battery_positions: Optional[List[Tuple[float, float, float]]] = None
    """The positions of the battery pads."""
    relocate_positions: Optional[List[Tuple[float, float, float]]] = None
    """The positions of the relocate pads."""
    boost_positions: Optional[List[Tuple[float, float, float]]] = None
    """U14 only: boost pad positions (+200 pts one-time each)."""
    room_polygons: Optional[List[List[Tuple[float, float]]]] = None
    """The polygons of the rooms."""
    points_per_percent: int = 40
    """Points awarded per percent of floor cleaned."""
    boost_pad_points: int = 200
    """U14 only: points when robot reaches unused boost pad."""
    relocate_penalty: int = 40
    """Points deducted when relocate is used."""

    @classmethod
    def from_node(cls, node: Node) -> "SupervisorConfig":
        """Build a SupervisorConfig from the CONFIG node in the world."""
        config = cls()

        # Basic scalar / vector fields
        subleague_field = node.getField("subleague")
        if subleague_field is not None:
            config.subleague = subleague_field.getSFString().strip()

        battery_field = node.getField("batteryPositions")
        if battery_field is not None:
            try:
                config.battery_positions = [
                    battery_field.getMFVec3f(i) for i in range(battery_field.getCount())
                ]
            except Exception:
                config.battery_positions = None

        relocate_field = node.getField("relocatePositions")
        if relocate_field is not None:
            try:
                config.relocate_positions = [
                    relocate_field.getMFVec3f(i) for i in range(relocate_field.getCount())
                ]
            except Exception:
                config.relocate_positions = None

        boost_field = node.getField("boostPositions")
        if boost_field is not None:
            try:
                config.boost_positions = [
                    boost_field.getMFVec3f(i) for i in range(boost_field.getCount())
                ]
            except Exception:
                config.boost_positions = None

        room_polygons_field = node.getField("roomVertices")
        if room_polygons_field is not None:
            polys: List[List[Tuple[float, float, float]]] = []
            try:
                count = room_polygons_field.getCount()
                for i in range(count):
                    room_node = room_polygons_field.getMFNode(i)
                    if room_node is None:
                        continue
                    vertices_field = room_node.getField("vertices")
                    if vertices_field is None:
                        continue
                    verts: List[Tuple[float, float, float]] = []
                    for j in range(vertices_field.getCount()):
                        v = vertices_field.getMFVec3f(j)
                        verts.append((float(v[0]), float(v[1]), float(v[2])))
                    if verts:
                        polys.append(verts)
            except Exception:
                polys = []

            if polys:
                config.room_polygons = [
                    [(vx, vy) for (vx, vy, _vz) in poly] for poly in polys
                ]

        return config

@dataclass
class RoomGrid:
    """The grid of the rooms."""
    grid: List[List[int]]
    """The grid of the rooms."""
    total_cells: List[int]
    """The total cells of the rooms."""
    cleaned_cells: List[int]
    """The cleaned cells of the rooms."""


class Supervisor(BaseSupervisor):
    is_running: Optional[bool] = None
    robot: Optional[Node] = None
    robot_team_name: Optional[str] = None
    battery_level: float = 100.0
    cleaned_squares_count: int = 0
    start_time: Optional[float] = None
    last_update_time: float = 0.0
    used_boost_pads: List[bool] = None
    score_log: List[dict] = None
    _last_yaw: Optional[float] = None
    _last_wiggle_reset: float = 0.0
    

    def __init__(self):
        super().__init__()

        self.ground_display = self.getDevice("ground_display")
        
        config_node = self.getFromDef("CONFIG")
        if config_node is None:
            raise ValueError("CONFIG node not found")
        self.config = SupervisorConfig.from_node(config_node)
        self.config.ground_size = (self.ground_display.getWidth(), self.ground_display.getHeight())

        self.battery_level = 100.0
        self.cleaned_squares_count = 0
        self.used_boost_pads = [False] * len(self.config.boost_positions or [])
        self.score_log = []

        # Initial dusty floor
        self.ground_display.setColor(0xfce1c7)
        self.ground_display.fillRectangle(0, 0, self.config.ground_size[X], self.config.ground_size[Y])
        self.ground_display.setAlpha(0.0)

        self.ground_grid = [
            [False for _ in range(self.config.ground_size[X] // self.config.ground_cell_size)] 
            for _ in range(self.config.ground_size[Y] // self.config.ground_cell_size)
        ]
        if self.config.subleague in ["U14", "FS"] and self.config.room_polygons:
            self.room_grid = self._make_room_grid()
        else:
            self.room_grid = None

        self._add_pads()

        # Start local web dashboard (http://localhost:8000/)
        self.dashboard_server = start_server(port=8000)
        set_subleague(self.config.subleague or "")


    def _make_room_grid(self) -> RoomGrid:
        """Make the room grid."""

        room_grid = RoomGrid(
            grid=[[-1 for _ in range(len(self.ground_grid[0]))] for _ in range(len(self.ground_grid))],
            total_cells=[0] * max(len(self.config.room_polygons), 1),
            cleaned_cells=[0] * max(len(self.config.room_polygons), 1)
        )
        for iy in range(len(self.ground_grid)):
            for ix in range(len(self.ground_grid[0])):
                wx, wy = helpers._cell_center_to_world(ix, iy, self.config.ground_cell_size, self.config.ground_size)
                for r, poly in enumerate(self.config.room_polygons):
                    if helpers._point_in_polygon(wx, wy, poly):
                        room_grid.grid[iy][ix] = r
                        room_grid.total_cells[r] += 1
                        break
        return room_grid


    def _add_pads(self):
        """Add battery and relocate pads to the house."""
        house = self.getFromDef("HOUSE")
        if house is None:
            return
        children_field = house.getField("children")

        for i in reversed(range(children_field.getCount())):
            node = children_field.getMFNode(i)
            name_field = node.getField("name") if node is not None else None
            if name_field is not None and name_field.getSFString().startswith("BATTERY_PAD"):
                children_field.removeMF(i)
            if name_field is not None and name_field.getSFString().startswith("RELOCATE_PAD"):
                children_field.removeMF(i)
            if name_field is not None and name_field.getSFString().startswith("BOOST_PAD"):
                children_field.removeMF(i)

        if self.config.subleague == "U19" and self.config.battery_positions:
            for pad_x, pad_y, pad_z in self.config.battery_positions:
                pad_string = (
                    'BatteryPad { '
                    f'translation {pad_x} {pad_y} {pad_z} '
                    'name "BATTERY_PAD" '
                    '}'
                )
                children_field.importMFNodeFromString(-1, pad_string)

        if self.config.relocate_positions:
            for pad_x, pad_y, pad_z in self.config.relocate_positions:
                pad_string = (
                    'RelocatePad { '
                    f'translation {pad_x} {pad_y} {pad_z} '
                    'name "RELOCATE_PAD" '
                    '}'
                )
                children_field.importMFNodeFromString(-1, pad_string)

        if self.config.subleague in ["U14", "FS"] and self.config.boost_positions:
            for pad_x, pad_y, pad_z in self.config.boost_positions:
                pad_string = (
                    'BoostPad { '
                    f'translation {pad_x} {pad_y} {pad_z} '
                    'name "BOOST_PAD" '
                    '}'
                )
                children_field.importMFNodeFromString(-1, pad_string)

    def _add_robot(self):
        """Add the robot to the scene."""
        root = self.getRoot()
        children_field = root.getField("children")

        robot_string = (
            'DEF VACUUM Create { '
            f'  translation {self.config.robot_translation[X]} {self.config.robot_translation[Y]} {self.config.robot_translation[Z]} '
            '  rotation 0 0 1 0 '
            '  controller "robot" '
            f'  subleague "{self.config.subleague}" '
            "}"
        )
        children_field.importMFNodeFromString(-1, robot_string)

        self.robot = self.getFromDef("VACUUM")


    def _get_team_name(self) -> Optional[str]:
        """Get the team name from the robot customData JSON or legacy 'team:Name'."""
        if self.robot is None:
            return None
        try:
            custom_data_field = self.robot.getField("customData")
            raw = (custom_data_field.getSFString() or "").strip()
        except Exception:
            return None

        # JSON format: {"team": "Name", ...}
        try:
            data = json.loads(raw)
            team = data.get("team")
            if team:
                return str(team).strip()
        except (json.JSONDecodeError, TypeError):
            pass

        # Legacy formats: "team:Name" or "..., team:Name, ..."
        if raw.startswith("team:"):
            return raw.split(":", 1)[1].strip()
        for part in raw.split(","):
            part = part.strip()
            if part.startswith("team:"):
                return part.split(":", 1)[1].strip()

        return None


    def _update_cleaning(self, translation: Tuple[float, float, float]) -> float:
        """Draw on the display and update the cleaning grid; return cleaned ratio [0,1]."""
        # Convert world position to grid indices
        ix, iy = helpers._world_to_grid(
            translation[X],
            translation[Y],
            self.config.ground_cell_size,
            self.config.ground_size,
        )

        # Convert grid index back to pixel center for drawing
        x = (ix + 0.5) * self.config.ground_cell_size
        y = (iy + 0.5) * self.config.ground_cell_size

        # Draw cleaned area (overwrite dust with clean color)
        self.ground_display.fillOval(int(x), int(y), self.config.ground_clean_radius, self.config.ground_clean_radius)
        cell_radius = int(math.ceil(self.config.ground_clean_radius / self.config.ground_cell_size))

        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                px_offset = dx * self.config.ground_cell_size
                py_offset = dy * self.config.ground_cell_size
                if px_offset * px_offset + py_offset * py_offset > self.config.ground_clean_radius * self.config.ground_clean_radius:
                    continue
                cx = ix + dx
                cy = iy + dy
                if 0 <= cx < len(self.ground_grid[0]) and 0 <= cy < len(self.ground_grid):
                    if not self.ground_grid[cy][cx]:
                        self.ground_grid[cy][cx] = True
                        self.cleaned_squares_count += 1
                        if self.room_grid is not None:
                            r_id = self.room_grid.grid[cy][cx]
                            if r_id >= 0:
                                self.room_grid.cleaned_cells[r_id] += 1
        
        total_squares = len(self.ground_grid) * len(self.ground_grid[0])
        cleaned_ratio = self.cleaned_squares_count / total_squares if total_squares > 0 else 0.0
        return cleaned_ratio


    def _update_room_cleaning(self, translation: Tuple[float, float, float]) -> Tuple[int, List[Tuple[int, float]]]:
        """Compute current room and per-room cleaning percentages."""
        current_room = -1
        room_pcts = []
        if self.room_grid is not None:
            gx, gy = helpers._world_to_grid(
                translation[X],
                translation[Y],
                self.config.ground_cell_size,
                self.config.ground_size,
            )
            if 0 <= gx < len(self.ground_grid[0]) and 0 <= gy < len(self.ground_grid):
                current_room = self.room_grid.grid[gy][gx]
            for r in range(len(self.room_grid.total_cells)):
                total_r = self.room_grid.total_cells[r]
                if total_r > 0:
                    pct = 100.0 * self.room_grid.cleaned_cells[r] / total_r
                else:
                    pct = 0.0
                room_pcts.append((r, pct))
        return current_room, room_pcts


    def _update_boost(self, translation: Tuple[float, float, float]) -> None:
        """U14 only: +config.boost_pad_points when robot reaches unused boost pad (one-time per pad)."""
        if self.config.subleague != "U19" and self.config.boost_positions and self.used_boost_pads is not None:
            for i, (pad_x, pad_y, pad_z) in enumerate(self.config.boost_positions):
                if i >= len(self.used_boost_pads) or self.used_boost_pads[i]:
                    continue
                dx = translation[X] - pad_x
                dy = translation[Y] - pad_y
                dist = math.hypot(dx, dy)
                if dist <= self.config.boost_radius:
                    self.used_boost_pads[i] = True
                    self.score_log.append({"source": "boost", "points": self.config.boost_pad_points})

    def _update_battery(self, translation: Tuple[float, float, float], dt: float) -> None:
        """Simulate battery drain/recharge for U19; may set status to game_over and remove robot."""
        if self.config.subleague != "U19" or self.robot is None or not self.is_running:
            return
        # Drain
        self.battery_level = max(0.0, self.battery_level - self.config.battery_drain_rate * dt)
        # Recharge on any battery pad
        if self.config.battery_positions:
            for pad_x, pad_y, pad_z in self.config.battery_positions:
                dx = translation[X] - pad_x
                dy = translation[Y] - pad_y
                dist = math.hypot(dx, dy)
                if dist <= self.config.battery_charge_radius:
                    self.battery_level = 100.0
                    break
        # Game over on empty
        if self.battery_level <= 0.0:
            self._remove_robot()
            self.is_running = False


    def _update_data(self, cleaned_ratio: float, remaining: float, current_room: int, room_pcts: List[Tuple[int, float]]):
        """Send JSON to robot and update web dashboard (throttled in run loop)."""
        # Room stats for dashboard
        if self.room_grid is not None:
            set_room_stats({r: pct for r, pct in room_pcts}, current_room)

        # JSON payload for robot
        if self.robot is not None:
            payload = {}
            if self.config.subleague == "U19":
                payload["battery"] = round(self.battery_level, 2)
            if self.room_grid is not None:
                payload["roomNumbers"] = list(range(len(self.room_grid.total_cells)))
                payload["roomPcts"] = {r: round(pct, 2) for r, pct in room_pcts}
                payload["currentRoom"] = current_room
            if self.robot_team_name:
                payload["team"] = self.robot_team_name
            if payload:
                try:
                    data_field = self.robot.getField("customData")
                    data_field.setSFString(json.dumps(payload))
                except Exception:
                    pass

        total_score = 1000 + int(cleaned_ratio * 100.0 * self.config.points_per_percent) + sum(
            e["points"] for e in (self.score_log or [])
        )
        # Webots HUD label
        if not self.is_running:
            label_text = (
                f"Team: {self.robot_team_name} ({self.config.subleague})\n" if self.robot_team_name else ""
                f"Game over: {total_score} pts\n"
                f"{cleaned_ratio * 100:.1f}% cleaned"
            )
        else:
            label_text = f"Team: {self.robot_team_name} ({self.config.subleague})\n" if self.robot_team_name else ""
            label_text += f"Score: {total_score} pts\n"
            if self.config.subleague == "U19":
                label_text += f"Battery: {self.battery_level:.1f}%\n"
            if self.config.subleague in ["U14", "FS"] and self.room_grid is not None:
                label_text += f"Room {current_room}: {room_pcts[current_room][1]:.1f}%\n"
            label_text += f"Time left: {remaining:.1f}s"
        self.setLabel(
            0,
            label_text,
            0.01,
            0.05,
            0.08,
            0xFFFFFF,
            0.0,
            "Arial",
        )

        # Dashboard score/time
        set_battery(self.battery_level if self.config.subleague == "U19" else None)
        update_score(total_score, cleaned_ratio * 100.0, remaining, self.is_running == False, self.score_log or [])


    def _remove_robot(self) -> None:
        """Remove robot node and update status."""
        if self.robot is not None:
            try:
                self.robot.remove()
            except Exception:
                pass
        self.robot = None
        self.robot_team_name = None
        self.battery_level = 0.0
        self.is_running = False
        self._last_yaw = None


    def _relocate_robot(self) -> None:
        """Relocate robot to the closest pad when requested. Logs -config.relocate_penalty."""
        if self.robot is None or not self.config.relocate_positions:
            return
        try:
            tx, ty, _ = self.robot.getField("translation").getSFVec3f()
            best_pad = min(
                self.config.relocate_positions,
                key=lambda p: math.hypot(tx - p[X], ty - p[Y])
            )
            self.robot.getField("translation").setSFVec3f(list(best_pad))
            self.robot.resetPhysics()
            self.score_log.append({"source": "relocate", "points": -self.config.relocate_penalty})
        except Exception:
            pass


    def reset(self):
        self._remove_robot()
        self._add_pads()

        # Reset visual dust on ground
        self.ground_display.setAlpha(1.0)
        self.ground_display.setColor(0xfce1c7)
        self.ground_display.fillRectangle(0, 0, self.config.ground_size[X], self.config.ground_size[Y])
        self.ground_display.setAlpha(0.0)

        self.ground_grid = [
            [False for _ in range(len(self.ground_grid[0]))] for _ in range(len(self.ground_grid))
        ]
        self.cleaned_squares_count = 0
        self.score_log = []
        if self.room_grid is not None:
            self.room_grid.cleaned_cells = [0] * len(self.room_grid.total_cells)

        self.battery_level = 100.0
        self.used_boost_pads = [False] * len(self.config.boost_positions or [])
        self.start_time = self.getTime()
        self.last_update_time = 0.0
        self.is_running = True
        self._last_yaw = None
        self._last_wiggle_reset = 0.0

        self._add_robot()


    def run(self):
        """Main control loop: wait for dashboard 'Run' then run a timed game."""
        while self.step(self.config.time_step) != -1:
            _ = consume_new_code_flag()

            # Handle start-of-run from dashboard
            if not self.is_running and consume_run_request():
                self.reset()
                continue

            # Handle relocate button
            if self.is_running and consume_relocate_request():
                self._relocate_robot()

            # Handle End button
            if self.is_running and consume_end_request():
                total_squares = len(self.ground_grid) * len(self.ground_grid[0])
                cleaned_ratio = self.cleaned_squares_count / total_squares if total_squares > 0 else 0.0
                total_score = int(cleaned_ratio * 100.0 * self.config.points_per_percent) + sum(
                    e["points"] for e in (self.score_log or [])
                )
                self._remove_robot()
                set_battery(None)
                update_score(total_score, cleaned_ratio * 100.0, 0.0, True, self.score_log or [])

            if not self.is_running or self.robot is None:
                continue

            now = self.getTime()
            translation = self.robot.getField("translation").getSFVec3f()


            # Team name from robot customData
            team_name = self._get_team_name()
            if team_name is not None:
                self.robot_team_name = team_name
                set_team_name(team_name)

            # Cleaning and room stats
            cleaned_ratio = self._update_cleaning(translation)
            current_room, room_pcts = self._update_room_cleaning(translation)

            # Time and battery / game-over logic
            elapsed = now - (self.start_time or now)
            remaining = max(0.0, self.config.run_time_limit - elapsed)
            if self.config.subleague == "U19":
                self._update_battery(translation, self.config.time_step / 1000.0)
            elif self.config.subleague in ["U14", "FS"]:
                self._update_boost(translation)
            if remaining <= 0.0 and self.is_running:
                self._remove_robot()
                remaining = 0.0

            # JSON + dashboard throttled to ~1 Hz
            if self.last_update_time == 0.0 or now - self.last_update_time >= 1.0:
                self._update_data(cleaned_ratio, remaining, current_room, room_pcts)
                self.last_update_time = now


supervisor = Supervisor()
supervisor.run()
