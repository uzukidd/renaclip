"""Pure answer sizing tests; no Tk root, desktop, credentials, or network."""
import unittest

from interfaces.answer_layout import (
    answer_content_height, compute_answer_bounds, measure_wrapped_lines,
)


class WrappedLineTests(unittest.TestCase):
    def test_empty_explicit_newlines_and_crlf(self):
        self.assertEqual(measure_wrapped_lines("", 10, len), 1)
        self.assertEqual(measure_wrapped_lines("one\ntwo\n", 10, len), 3)
        self.assertEqual(measure_wrapped_lines("one\r\ntwo", 10, len), 2)

    def test_words_move_to_next_line(self):
        self.assertEqual(measure_wrapped_lines("abc def ghi", 5, len), 3)
        self.assertEqual(measure_wrapped_lines("ab cd", 5, len), 1)

    def test_long_word_and_cjk(self):
        self.assertEqual(measure_wrapped_lines("abcdefghijk", 5, len), 3)
        self.assertEqual(measure_wrapped_lines("\u4e2d\u6587\u6d4b\u8bd5\u6587", 4, lambda text: len(text) * 2), 3)
        self.assertEqual(measure_wrapped_lines("abcdef", 0.5, len), 6)

    def test_bounded_work_and_glyph_cache(self):
        calls = []
        def measure(text):
            calls.append(text)
            return len(text)
        self.assertEqual(measure_wrapped_lines("a" * 1000000, 5, measure, max_lines=3), 3)
        self.assertEqual(calls, ["a"])
        self.assertEqual(measure_wrapped_lines("x" * 100, 10, lambda _: 0,
                                             max_chars=8, max_lines=9), 9)

    def test_invalid_measurement_inputs(self):
        for width in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                measure_wrapped_lines("text", width, len)
        with self.assertRaises(ValueError):
            measure_wrapped_lines("text", 20, len, max_lines=0)


class AnswerBoundsTests(unittest.TestCase):
    def test_grows_from_compact_to_max_without_moving_question(self):
        question = (100, 100, 460, 196)
        panel = (100, 100, 460, 900)
        heights = []
        for lines in (1, 5, 15, 10000):
            result = compute_answer_bounds(question, panel, answer_content_height(lines, 18))
            self.assertEqual(result[:3], (100, 206, 460))
            heights.append(result[3] - result[1])
        self.assertEqual(heights, [90, 138, 318, 560])
        self.assertEqual(question, (100, 100, 460, 196))

    def test_dpi_uses_physical_line_height_only_once(self):
        self.assertEqual(answer_content_height(5, 27, 1.5), 207)
        result = compute_answer_bounds((0, 0, 540, 144), (0, 0, 540, 1500), 1, 1.5)
        self.assertEqual(result, (0, 159, 540, 294))
        capped = compute_answer_bounds((0, 0, 540, 144), (0, 0, 540, 1500), 99999, 1.5)
        self.assertEqual(capped[3] - capped[1], 840)

    def test_negative_monitor_and_bottom_space_clamp(self):
        result = compute_answer_bounds((-1700, -100, -1340, -4), (-1750, -150, -1300, 250), 900)
        self.assertEqual(result, (-1700, 6, -1340, 250))

    def test_narrow_and_minimal_space(self):
        result = compute_answer_bounds((0, 0, 400, 100), (20, 0, 200, 101), 500)
        self.assertEqual(result, (20, 100, 200, 101))
        self.assertIsNone(compute_answer_bounds((0, 0, 100, 100), (0, 0, 100, 100), 500))
        self.assertIsNone(compute_answer_bounds((200, 0, 400, 100), (0, 0, 100, 500), 500))

    def test_invalid_geometry_and_height(self):
        for scale in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                compute_answer_bounds((0, 0, 100, 100), (0, 0, 100, 500), 50, scale)
        with self.assertRaises(ValueError):
            compute_answer_bounds((0, 0, 0, 100), (0, 0, 100, 500), 50)
        with self.assertRaises(ValueError):
            answer_content_height(2, 0)
        with self.assertRaises(ValueError):
            compute_answer_bounds((0, 0, 100, 100), (0, 0, 100, 500), -1)


if __name__ == "__main__":
    unittest.main()
