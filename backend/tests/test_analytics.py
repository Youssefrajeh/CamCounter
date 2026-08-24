import unittest
from backend.config import Point, TripwireLine, OccupancyZone
from backend.analytics import segments_intersect, point_in_polygon, SpatialAnalytics, TrackedObject


class TestAnalyticsMath(unittest.TestCase):
    def test_segments_intersect(self):
        # Crossing segments: (0, 0)->(2, 2) and (0, 2)->(2, 0)
        self.assertTrue(segments_intersect((0, 0), (2, 2), (0, 2), (2, 0)))
        
        # Parallel non-intersecting
        self.assertFalse(segments_intersect((0, 0), (2, 0), (0, 1), (2, 1)))

    def test_point_in_polygon(self):
        # Square: (0,0), (10,0), (10,10), (0,10)
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        
        # Inside
        self.assertTrue(point_in_polygon(5, 5, poly))
        # Outside
        self.assertFalse(point_in_polygon(15, 5, poly))
        self.assertFalse(point_in_polygon(5, 15, poly))

    def test_spatial_analytics_tripwire(self):
        line = TripwireLine(
            id="test_line",
            name="Door",
            p1=Point(x=0.5, y=0.0),
            p2=Point(x=0.5, y=1.0),
            color="#10B981"
        )
        analytics = SpatialAnalytics([line], [])
        
        # Person moving from left (0.4, 0.5) to right (0.6, 0.5)
        obj = TrackedObject(track_id=1, bbox=(0.35, 0.4, 0.45, 0.5), conf=0.9)
        obj.update(bbox=(0.55, 0.4, 0.65, 0.5), conf=0.9)
        
        tracks = {1: obj}
        summary = analytics.process_tracks(tracks)
        
        self.assertEqual(summary["current_occupancy"], 1)
        self.assertEqual(summary["total_in"] + summary["total_out"], 1)


if __name__ == "__main__":
    unittest.main()
