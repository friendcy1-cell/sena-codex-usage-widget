import unittest
from screens import clamp, geometry, nearest_area, virtual_bounds


class ScreenTests(unittest.TestCase):
    areas = [(-1920, 0, 0, 1040), (0, 0, 3840, 2120), (3840, -300, 5760, 780)]

    def test_all_three_screens_can_be_selected(self):
        for area in self.areas:
            x, y = (area[0] + area[2]) // 2, (area[1] + area[3]) // 2
            self.assertEqual(nearest_area(self.areas, x, y), area)

    def test_negative_coordinates_preserved(self):
        self.assertEqual(clamp(self.areas[0], -1800, 80, 300, 246), (-1800, 80))
        self.assertEqual(clamp(self.areas[2], 4000, -200, 300, 246), (4000, -200))

    def test_drag_not_stuck_at_primary_boundary(self):
        self.assertEqual(clamp(virtual_bounds(self.areas), 4100, 20, 300, 246), (4100, 20))
        self.assertEqual(clamp(virtual_bounds(self.areas), -600, 20, 300, 246), (-600, 20))

    def test_removed_monitor_position_recovered(self):
        area = nearest_area([self.areas[1]], -1800, 200)
        self.assertEqual(clamp(area, -1800, 200, 300, 246), (0, 200))

    def test_panel_within_work_area(self):
        for a in self.areas:
            x, y = clamp(a, a[0] - 500, a[3], 440, 700)
            self.assertGreaterEqual(x, a[0])
            self.assertGreaterEqual(y, a[1])
            self.assertLessEqual(x + 440, a[2])
            self.assertLessEqual(y + 700, a[3])

    def test_negative_position_not_right_edge_offset(self):
        self.assertEqual(geometry(-1800, -200, 300, 246), "300x246+-1800+-200")

    def test_oversized_window_remains_accessible(self):
        self.assertEqual(clamp((0, 0, 320, 200), 80, 80, 440, 700), (0, 0))


if __name__ == "__main__":
    unittest.main()
