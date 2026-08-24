import math
import time
from typing import List, Tuple, Dict, Set, Optional, Any
from collections import defaultdict, deque
from backend.config import Point, TripwireLine, OccupancyZone


def ccw(A: Tuple[float, float], B: Tuple[float, float], C: Tuple[float, float]) -> bool:
    """Tests whether points A, B, C are listed in counterclockwise order."""
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])


def segments_intersect(A: Tuple[float, float], B: Tuple[float, float],
                       C: Tuple[float, float], D: Tuple[float, float]) -> bool:
    """Returns True if line segment AB and line segment CD intersect."""
    return (ccw(A, C, D) != ccw(B, C, D)) and (ccw(A, B, C) != ccw(A, B, D))


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """
    Ray-casting algorithm to test whether point (x, y) is inside polygon.
    Polygon is a list of (x, y) tuples.
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside


class TrackedObject:
    def __init__(self, track_id: int, bbox: Tuple[float, float, float, float], conf: float):
        self.track_id = track_id
        self.bbox = bbox  # x1, y1, x2, y2 (normalized 0.0 - 1.0)
        self.conf = conf
        self.history: deque = deque(maxlen=60)  # Stores (cx, cy, timestamp)
        self.first_seen = time.time()
        self.last_seen = time.time()
        
        cx = (bbox[0] + bbox[2]) / 2.0
        # Bottom-center is best representative point for ground/floor person location
        cy = bbox[3]
        self.history.append((cx, cy, self.last_seen))

    def update(self, bbox: Tuple[float, float, float, float], conf: float):
        self.bbox = bbox
        self.conf = conf
        self.last_seen = time.time()
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = bbox[3]  # Bottom center (foot position)
        self.history.append((cx, cy, self.last_seen))

    @property
    def centroid(self) -> Tuple[float, float]:
        if not self.history:
            return ((self.bbox[0] + self.bbox[2]) / 2.0, self.bbox[3])
        return (self.history[-1][0], self.history[-1][1])

    @property
    def prev_centroid(self) -> Optional[Tuple[float, float]]:
        if len(self.history) >= 2:
            return (self.history[-2][0], self.history[-2][1])
        return None


class SpatialAnalytics:
    def __init__(self, lines: List[TripwireLine], zones: List[OccupancyZone]):
        self.lines: Dict[str, TripwireLine] = {line.id: line for line in lines}
        self.zones: Dict[str, OccupancyZone] = {zone.id: zone for zone in zones}
        
        # Track crossing state to avoid double counting: line_id -> {track_id: (timestamp, 'IN'|'OUT')}
        self.crossed_tracks: Dict[str, Dict[int, float]] = defaultdict(dict)
        self.crossing_cooldown = 2.0  # seconds cooldown before same track can cross line again
        
        # Zone tracking state: zone_id -> {track_id: entry_timestamp}
        self.zone_occupants: Dict[str, Dict[int, float]] = defaultdict(dict)
        
        # Overall counts
        self.total_in = 0
        self.total_out = 0
        self.peak_occupancy = 0

    def update_config(self, lines: List[TripwireLine], zones: List[OccupancyZone]):
        """Update active tripwires and zones without resetting cumulative stats."""
        new_lines = {}
        for line in lines:
            if line.id in self.lines:
                # Preserve existing counts if already tracked
                line.in_count = self.lines[line.id].in_count
                line.out_count = self.lines[line.id].out_count
            new_lines[line.id] = line
        self.lines = new_lines

        new_zones = {}
        for zone in zones:
            if zone.id in self.zones:
                zone.peak_count = self.zones[zone.id].peak_count
            new_zones[zone.id] = zone
        self.zones = new_zones

    def process_tracks(self, tracks: Dict[int, TrackedObject]) -> Dict[str, Any]:
        """
        Process all active tracked objects against configured lines and zones.
        Returns live analytics summary dictionary.
        """
        current_time = time.time()
        current_occupancy = len(tracks)
        if current_occupancy > self.peak_occupancy:
            self.peak_occupancy = current_occupancy

        # 1. Clean up old line crossing records
        for line_id in list(self.crossed_tracks.keys()):
            self.crossed_tracks[line_id] = {
                tid: ts for tid, ts in self.crossed_tracks[line_id].items()
                if current_time - ts < self.crossing_cooldown
            }

        # 2. Check Line Crossings
        line_crossing_events = []
        for line_id, line in self.lines.items():
            if not line.active:
                continue

            A = (line.p1.x, line.p1.y)
            B = (line.p2.x, line.p2.y)

            # Normal vector to line AB pointing to "IN" side
            # Line vector: (Bx - Ax, By - Ay)
            # Normal vector N: (-(By - Ay), Bx - Ax)
            dx = B[0] - A[0]
            dy = B[1] - A[1]
            nx = -dy
            ny = dx

            for track_id, track in tracks.items():
                if len(track.history) < 2:
                    continue

                # Check if this track recently crossed this line
                if track_id in self.crossed_tracks[line_id]:
                    continue

                # Test trajectory segment over last few points
                P = (track.history[-2][0], track.history[-2][1])
                Q = (track.history[-1][0], track.history[-1][1])

                if segments_intersect(A, B, P, Q):
                    # Motion vector
                    mx = Q[0] - P[0]
                    my = Q[1] - P[1]

                    # Dot product with normal
                    dot = mx * nx + my * ny

                    if dot > 0:
                        # Moving IN
                        line.in_count += 1
                        self.total_in += 1
                        direction = "IN"
                    else:
                        # Moving OUT
                        line.out_count += 1
                        self.total_out += 1
                        direction = "OUT"

                    self.crossed_tracks[line_id][track_id] = current_time
                    line_crossing_events.append({
                        "line_id": line_id,
                        "line_name": line.name,
                        "track_id": track_id,
                        "direction": direction,
                        "timestamp": current_time
                    })

        # 3. Check Zone Occupancy
        zone_counts = {}
        active_zone_occupants = defaultdict(dict)

        for zone_id, zone in self.zones.items():
            if not zone.active or len(zone.points) < 3:
                zone.current_count = 0
                zone_counts[zone_id] = 0
                continue

            poly = [(p.x, p.y) for p in zone.points]
            count = 0

            for track_id, track in tracks.items():
                cx, cy = track.centroid
                if point_in_polygon(cx, cy, poly):
                    count += 1
                    # Record entry time or keep existing
                    entry_time = self.zone_occupants[zone_id].get(track_id, current_time)
                    active_zone_occupants[zone_id][track_id] = entry_time

            zone.current_count = count
            if count > zone.peak_count:
                zone.peak_count = count
            zone_counts[zone_id] = count

        self.zone_occupants = active_zone_occupants

        # Build lines state dict
        lines_summary = {
            lid: {
                "name": l.name,
                "in_count": l.in_count,
                "out_count": l.out_count,
                "total": l.in_count + l.out_count
            }
            for lid, l in self.lines.items()
        }

        # Build zones state dict
        zones_summary = {
            zid: {
                "name": z.name,
                "current_count": z.current_count,
                "peak_count": z.peak_count,
                "max_capacity": z.max_capacity,
                "occupancy_rate": round(z.current_count / max(1, z.max_capacity) * 100, 1)
            }
            for zid, z in self.zones.items()
        }

        return {
            "current_occupancy": current_occupancy,
            "total_in": self.total_in,
            "total_out": self.total_out,
            "net_flow": self.total_in - self.total_out,
            "peak_occupancy": self.peak_occupancy,
            "lines": lines_summary,
            "zones": zones_summary,
            "events": line_crossing_events
        }
