"""Pure QRect placement tests; no window, desktop capture or API requests."""
import unittest

from PySide6.QtCore import QRect

from interfaces.screenshot_layout import place_question, place_answer


class QuestionPlacementTests(unittest.TestCase):
    def test_right_aligned_only_question_height_clamped(self):
        area = QRect(0, 0, 1200, 900)
        selection = QRect(100, 700, 200, 100)
        result = place_question(selection, area)
        self.assertEqual(result, QRect(312, 700, 360, 96))
        self.assertFalse(result.intersects(selection))
        self.assertTrue(area.contains(result))

    def test_left_when_right_has_no_room(self):
        area = QRect(0, 0, 1200, 900)
        self.assertEqual(place_question(QRect(950, 700, 150, 100), area),
                         QRect(578, 700, 360, 96))

    def test_bottom_clamp_does_not_reserve_answer(self):
        result = place_question(QRect(30, 850, 200, 50), QRect(0, 0, 1200, 900))
        self.assertEqual(result.top(), 804)
        self.assertEqual(result.height(), 96)

    def test_no_sides_uses_largest_vertical_strip(self):
        area = QRect(0, 0, 700, 900)
        self.assertEqual(place_question(QRect(150, 120, 400, 100), area),
                         QRect(150, 232, 360, 96))
        self.assertEqual(place_question(QRect(150, 700, 400, 100), area),
                         QRect(150, 592, 360, 96))

    def test_negative_origin_and_small_screen(self):
        for area, selection in ((QRect(-1920, -300, 1920, 1080), QRect(-1700, 600, 200, 150)),
                                (QRect(-100, -60, 210, 130), QRect(-90, -50, 180, 110))):
            with self.subTest(area=area):
                original = QRect(selection)
                result = place_question(selection, area)
                self.assertTrue(area.contains(result))
                self.assertEqual(selection, original)

    def test_full_screen_fallback_is_contained(self):
        area = QRect(-200, 20, 400, 300)
        self.assertTrue(area.contains(place_question(area, area)))

    def test_invalid_inputs(self):
        for kwargs in ({'width': 0}, {'height': 0}, {'gap': -1}):
            with self.assertRaises(ValueError):
                place_question(QRect(1, 1, 10, 10), QRect(0, 0, 100, 100), **kwargs)
        with self.assertRaises(ValueError):
            place_question(QRect(), QRect(0, 0, 100, 100))
        with self.assertRaises(ValueError):
            place_question(QRect(1, 1, 10, 10), QRect())


class AnswerPlacementTests(unittest.TestCase):
    def check_placement(self, question, area, desired, **kwargs):
        original = QRect(question)
        result, direction = place_answer(question, area, desired, **kwargs)
        self.assertEqual(question, original)
        if not result.isEmpty():
            self.assertTrue(area.contains(result), (area, result))
            self.assertFalse(result.intersects(question), (question, result))
            self.assertLessEqual(result.height(), min(desired, kwargs.get('max_height', 560)))
        return result, direction

    def test_short_answer_below_growing_answer_above_without_moving_question(self):
        area = QRect(0, 0, 1200, 900)
        question = place_question(QRect(100, 650, 200, 100), area)
        small, direction = self.check_placement(question, area, 100)
        self.assertEqual(direction, 'below')
        self.assertEqual(small.top(), question.bottom() + 11)
        large, direction = self.check_placement(question, area, 500)
        self.assertEqual(direction, 'above')
        self.assertEqual(large.bottom(), question.top() - 11)
        self.assertEqual(question.top(), 650)

    def test_top_and_bottom_corners(self):
        area = QRect(0, 0, 1200, 900)
        for question, expected in ((QRect(0, 0, 360, 96), 'below'),
                                   (QRect(840, 804, 360, 96), 'above')):
            result, direction = self.check_placement(question, area, 300)
            self.assertEqual(direction, expected)
            self.assertEqual(result.width(), 360)
            self.assertEqual(result.height(), 300)

    def test_larger_vertical_strip_caps_height(self):
        result, direction = self.check_placement(QRect(300, 380, 360, 96), QRect(0, 0, 1200, 900), 800)
        self.assertEqual(direction, 'below')
        self.assertEqual(result.height(), 414)

    def test_maximum_and_negative_screen_origin(self):
        area = QRect(-1920, -400, 1920, 1400)
        result, direction = self.check_placement(QRect(-1800, -350, 360, 96), area, 10000)
        self.assertEqual(result.height(), 560)
        self.assertEqual(direction, 'below')

    def test_preferred_direction_stays_when_it_fits(self):
        question, area = QRect(300, 400, 360, 96), QRect(0, 0, 1200, 1000)
        _, direction = self.check_placement(question, area, 120, preferred_direction='above')
        self.assertEqual(direction, 'above')
        _, direction = self.check_placement(question, area, 450, preferred_direction='above')
        self.assertEqual(direction, 'below')

    def test_side_fallback_when_both_vertical_strips_too_small(self):
        area = QRect(0, 0, 1200, 240)
        result, direction = self.check_placement(QRect(300, 72, 360, 96), area, 200)
        self.assertEqual(direction, 'right')
        self.assertEqual(result, QRect(670, 40, 360, 200))
        result, direction = self.check_placement(QRect(840, 72, 360, 96), area, 200)
        self.assertEqual(direction, 'left')

    def test_no_space_returns_empty_instead_of_covering_question(self):
        area = QRect(-50, -20, 100, 80)
        result, _ = self.check_placement(area, area, 300)
        self.assertTrue(result.isEmpty())

    def test_narrow_answer_and_custom_gap(self):
        area = QRect(-100, 0, 200, 450)
        result, direction = self.check_placement(QRect(-90, 100, 180, 96), area, 120, gap=20)
        self.assertEqual(direction, 'below')
        self.assertEqual(result, QRect(-90, 216, 180, 120))

    def test_inputs_are_not_mutated_for_many_monitor_edges(self):
        area = QRect(-800, -200, 1400, 950)
        for x in (-800, -400, 230):
            for y in (-200, 100, 644):
                for height in (60, 150, 560, 900):
                    with self.subTest(x=x, y=y, height=height):
                        self.check_placement(QRect(x, y, 360, 96), area, height)

    def test_invalid_answer_inputs(self):
        for question, area, desired, kwargs in ((QRect(), QRect(0, 0, 100, 100), 40, {}),
                                               (QRect(0, 0, 20, 20), QRect(), 40, {}),
                                               (QRect(0, 0, 20, 20), QRect(0, 0, 100, 100), 0, {}),
                                               (QRect(0, 0, 20, 20), QRect(0, 0, 100, 100), 40, {'max_height': 0})):
            with self.assertRaises(ValueError):
                place_answer(question, area, desired, **kwargs)


if __name__ == '__main__':
    unittest.main()
