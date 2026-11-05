"""Exercise real offline TeX rendering and its Markdown/clipboard integration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
import base64
import json
import xml.etree.ElementTree as ET

from PySide6.QtCore import QUrl, qInstallMessageHandler
from PySide6.QtGui import QImage, QTextCursor, QTextDocument, QFontDatabase
from PySide6.QtWidgets import QApplication
from interfaces.formula_rendering import protect_formulas, qt_compatible_svg
from interfaces.screenshot_overlay import AnswerBrowser

APP = QApplication.instance() or QApplication([])
# Qt's Markdown writer resolves bold/italic through actual font faces. The
# Windows offscreen platform does not discover system fonts automatically.
if os.name == 'nt':
    from pathlib import Path
    for name in ('segoeui.ttf', 'segoeuib.ttf', 'segoeuii.ttf', 'segoeuiz.ttf'):
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))


def images(document):
    result = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.charFormat().isImageFormat():
                result.append(fragment.charFormat().toImageFormat())
            iterator += 1
        block = block.next()
    return result


class FormulaTests(unittest.TestCase):
    def setUp(self):
        self.browser = AnswerBrowser()
        self.browser.resize(310, 240)
        self.browser.show()
        self.addCleanup(self.browser.close)

    def test_all_delimiters_and_real_svg_pixels(self):
        source = r"Inline \(\hat{x}_i^m\) and $x^2$." + "\n\n" + r"\[\begin{bmatrix}x\\y\\1\end{bmatrix}\]" + "\n\n" + r"$$\frac{a}{b}$$"
        self.browser.setMarkdown(source)
        doc = self.browser.document()
        formats = images(doc)
        self.assertEqual(len(formats), 4)
        self.assertNotIn("RENACLIPMATH", doc.toPlainText())
        self.assertNotIn(r"\begin", doc.toPlainText())
        for fmt in formats:
            image = doc.resource(QTextDocument.ResourceType.ImageResource, QUrl(fmt.name()))
            self.assertFalse(image.isNull())
            self.assertGreater(fmt.width(), 0)
            self.assertTrue(any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height())))
        # A three-row matrix must have visibly more height than inline math.
        self.assertGreater(formats[2].height(), formats[0].height() * 2)

    def test_stretched_brackets_have_visible_cropped_viewports(self):
        renderer = self.browser.formula_renderer
        renderer._initialize()
        tex = r"\begin{bmatrix}\cos(-\theta_m)\\\sin(-\theta_m)\\1\end{bmatrix}"
        from PySide6.QtQml import QJSValue
        result = renderer._engine.globalObject().property("renderFormula").call([QJSValue(tex), QJSValue(True)])
        svg = json.loads(result.toString())["svg"].replace("currentColor", "#f3edf1")
        root = ET.fromstring(qt_compatible_svg(svg, 14 / 1000))
        namespace = "{http://www.w3.org/2000/svg}"
        self.assertEqual(len(list(root.iter(namespace + "svg"))), 1)
        cropped = list(root.iter(namespace + "image"))
        self.assertTrue(cropped)
        for image_node in cropped:
            payload = image_node.get("{http://www.w3.org/1999/xlink}href").split(",", 1)[1]
            image = QImage.fromData(base64.b64decode(payload))
            self.assertFalse(image.isNull())
            self.assertTrue(any(image.pixelColor(x, y).alpha() for x in range(image.width()) for y in range(image.height())))
        warnings = []
        previous = qInstallMessageHandler(lambda kind, context, message: warnings.append(message))
        try:
            renderer.render(tex, True, "#f3edf1", 14, 2)
        finally:
            qInstallMessageHandler(previous)
        self.assertEqual(warnings, [])

    def test_streaming_incomplete_formula_is_protected_then_renders(self):
        partial = r"\[\begin{bmatrix}" + "\nx_i \\\n" + "---\ny_i"
        self.browser.setMarkdown(partial)
        doc = self.browser.document()
        self.assertEqual(images(doc), [])
        self.assertEqual(doc.toPlainText(), partial)
        block = doc.begin()
        while block.isValid():
            self.assertEqual(block.blockFormat().headingLevel(), 0)
            block = block.next()
        complete = r"\[\begin{bmatrix}x_i\\y_i\end{bmatrix}\]"
        self.browser.setMarkdown(complete)
        self.assertEqual(len(images(doc)), 1)

    def test_literal_code_and_escaped_dollars_are_preserved(self):
        source = r"`\(x^2\)` and \$5" + "\n\n```latex\n" + r"\[\begin{bmatrix}x\end{bmatrix}\]" + "\n```\n\n" + r"$y$"
        self.browser.setMarkdown(source)
        self.assertEqual(len(images(self.browser.document())), 1)
        self.assertIn(r"\(x^2\)", self.browser.toPlainText())
        self.assertIn(r"\begin{bmatrix}", self.browser.toPlainText())
        _, formulas = protect_formulas("~~~latex\n$x$\n")
        self.assertEqual(formulas, [])

    def test_copy_preserves_original_latex_and_unicode(self):
        source = "中文 😀 " + r"\(\frac{a}{b}\)" + " end"
        self.browser.setMarkdown(source)
        self.browser.selectAll()
        mime = self.browser.createMimeDataFromSelection()
        self.assertEqual(mime.text(), '中文 😀 ' + r'$\frac{a}{b}$' + ' end')
        self.assertNotIn("renaclip-math:", mime.html())
        adjacent = r"\(x\)\(x\)"
        self.browser.setMarkdown(adjacent)
        self.browser.selectAll()
        self.assertEqual(self.browser.createMimeDataFromSelection().text(), '$x$$x$')

    def test_copy_entire_answer_preserves_exact_markdown_source(self):
        source = '# Heading\n\n**Bold** and [Source](https://example.com)\n\n- first\n- second\n\n```python\nprint(1)\n```\n\n' + r'\[\frac{a}{b}\]'
        self.browser.setMarkdown(source)
        self.browser.selectAll()
        mime = self.browser.createMimeDataFromSelection()
        expected = source.replace(r'\[\frac{a}{b}\]', '$$\n' + r'\frac{a}{b}' + '\n$$')
        self.assertEqual(mime.text(), expected)
        self.assertEqual(bytes(mime.data('text/markdown')).decode('utf-8'), expected)
        self.assertFalse(mime.hasHtml())

    def test_partial_copy_preserves_bold_links_and_latex(self):
        self.browser.setMarkdown('Before\n\n**Bold** and [Source](https://example.com) ' + r'\(x_i^2\)' + '\n\nAfter')
        document = self.browser.document()
        start = document.find('Bold').selectionStart()
        end = document.find('After').selectionStart() - 1
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.browser.setTextCursor(cursor)
        mime = self.browser.createMimeDataFromSelection()
        self.assertIn('**Bold**', mime.text())
        self.assertIn('[Source](https://example.com)', mime.text())
        self.assertIn(r'$x_i^2$', mime.text())
        self.assertNotIn('renaclip-math:', mime.text())
        self.assertNotIn('RENACLIPCOPY', mime.text())
        self.assertNotIn('Before', mime.text())
        self.assertNotIn('After', mime.text())
        self.assertFalse(mime.hasHtml())

    def test_copy_normalizes_math_but_keeps_code_and_unfinished_tex(self):
        source = r'Inline \(x^2\) and $y$.' + '\n\n' + r'\[a+b\]' + '\n\n' + r'`\(literal\)`' + '\n\n```latex\n' + r'\[literal\]' + '\n```\n\n' + r'\(unfinished'
        self.browser.setMarkdown(source)
        self.browser.selectAll()
        copied = self.browser.createMimeDataFromSelection().text()
        self.assertIn('Inline $x^2$ and $y$.', copied)
        self.assertIn('$$\na+b\n$$', copied)
        self.assertIn(r'`\(literal\)`', copied)
        self.assertIn('```latex\n' + r'\[literal\]' + '\n```', copied)
        self.assertTrue(copied.endswith(r'\(unfinished'))

    def test_partial_display_formula_uses_dollar_block(self):
        self.browser.setMarkdown('Before\n\n' + r'\[\frac{a}{b}\]' + '\n\nAfter')
        document = self.browser.document()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.charFormat().isImageFormat():
                    cursor = QTextCursor(document)
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + 1, QTextCursor.MoveMode.KeepAnchor)
                    self.browser.setTextCursor(cursor)
                    self.assertEqual(self.browser.createMimeDataFromSelection().text(), '$$\n' + r'\frac{a}{b}' + '\n$$')
                    return
                iterator += 1
            block = block.next()
        self.fail('Formula image was not found')

    def test_copy_selected_code_without_markdown_wrappers(self):
        code = 'def hello():\n    print("hello")\n    return 1'
        self.browser.setMarkdown('Before\n\n```python\n' + code + '\n```\n\nAfter')
        document = self.browser.document()
        cursor = QTextCursor(document)
        cursor.setPosition(document.find('def hello():').selectionStart())
        cursor.setPosition(document.find('return 1').selectionEnd(), QTextCursor.MoveMode.KeepAnchor)
        self.browser.setTextCursor(cursor)
        mime = self.browser.createMimeDataFromSelection()
        self.assertEqual(mime.text(), code)
        self.assertFalse(mime.hasFormat('text/markdown'))
        self.browser.setMarkdown('Run `print("hello")` now.')
        self.browser.setTextCursor(self.browser.document().find('print("hello")'))
        self.assertEqual(self.browser.createMimeDataFromSelection().text(), 'print("hello")')

    def test_answer_containing_only_code_copies_plain_code(self):
        self.browser.setMarkdown('```sh\necho hello\n```')
        self.browser.selectAll()
        self.assertEqual(self.browser.createMimeDataFromSelection().text(), 'echo hello')

    def test_plain_text_resets_previous_markdown_source(self):
        self.browser.setMarkdown('**old**')
        self.browser.setPlainText('**literal**')
        self.browser.selectAll()
        self.assertEqual(self.browser.createMimeDataFromSelection().text(), '**literal**')
        self.browser.clear()
        self.assertEqual(self.browser.createMimeDataFromSelection().text(), '')

    def test_invalid_tex_is_readable_and_does_not_break_markdown(self):
        source = r"\(\notARealCommand{x}\)"
        self.browser.setMarkdown("**Bold** " + source)
        self.assertEqual(images(self.browser.document()), [])
        self.assertIn(source, self.browser.toPlainText())
        self.assertGreater(self.browser.document().find("Bold").charFormat().fontWeight(), 400)

    def test_wide_matrix_has_horizontal_scroll(self):
        self.browser.setMarkdown(r"\[\begin{bmatrix}" + " & ".join([r"\frac{123456}{789}"] * 10) + r"\end{bmatrix}\]")
        APP.processEvents()
        self.assertEqual(len(images(self.browser.document())), 1)
        self.assertGreater(self.browser.horizontalScrollBar().maximum(), 0)

    def test_markdown_links_code_and_list_context(self):
        source = "[price](https://example.com/$x$) and $y$\n\n    $literal$\n\nUnmatched ` tick then $x^2$."
        self.browser.setMarkdown(source)
        doc = self.browser.document()
        self.assertEqual(doc.find("price").charFormat().anchorHref(), "https://example.com/$x$")
        self.assertIn("$literal$", doc.toPlainText())
        self.assertEqual(len(images(doc)), 2)
        self.browser.setMarkdown("- before\n  " + r"\[x\]" + "\n  after")
        self.assertEqual(len(images(doc)), 1)
        self.assertIsNotNone(doc.begin().textList())
        self.assertIsNotNone(doc.find("after").block().textList())

    def test_repeated_render_is_cached_and_clear_releases_sources(self):
        self.browser.setMarkdown(r"$x^2$")
        cache = self.browser.formula_renderer._cache
        self.assertEqual(len(cache), 1)
        self.browser.setMarkdown(r"$x^2$")
        self.assertEqual(len(cache), 1)
        self.browser.clear()
        self.assertFalse(self.browser.formula_sources)
        self.assertEqual(self.browser.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()
