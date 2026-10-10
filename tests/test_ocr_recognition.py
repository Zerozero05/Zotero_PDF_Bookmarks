"""Scanned-directory OCR regressions use synthetic PDFs and local fake outputs."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

import numpy as np
import pymupdf

import toc_generation as generation


def output(rows):
    boxes = np.array([[[20, 50 + i * 30], [260, 50 + i * 30],
                       [260, 70 + i * 30], [20, 70 + i * 30]] for i in range(len(rows))])
    return SimpleNamespace(boxes=boxes, txts=rows, scores=[.98] * len(rows))


class OCRRecognitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pdf-bookmarks-ocr-")
        self.addCleanup(temporary.cleanup)
        self.pdf = Path(temporary.name) / "扫描目录.pdf"
        with pymupdf.open() as document:
            for _ in range(5):
                document.new_page(width=300, height=400)
            document.save(self.pdf)
        self.original = self.pdf.read_bytes()

    def recognize(self, upright, classified):
        class Engine:
            use_cls = True

            def __call__(self, image, use_cls=None, **kwargs):
                if use_cls is not None:
                    self.use_cls = use_cls
                return output(classified if self.use_cls else upright)

        engine = Engine()
        with patch.object(generation, "_engine", return_value=engine):
            draft = generation.generate_toc(self.pdf, toc_start=1, toc_end=1, offset=1)
        self.assertEqual(self.pdf.read_bytes(), self.original)
        self.assertFalse(any(entry.confirmed for entry in draft.entries))
        self.assertTrue(engine.use_cls, "OCR retry must restore angle-classification state")
        return draft

    def test_upright_toc_rows_mistakenly_rotated_are_recovered(self):
        upright = ["Contents", "第一章 基础 .... 1", "§1.1 定义 .... 2", "第二章 应用 .... 3", "§2.1 示例 .... 4"]
        classified = ["Contents", "第一章 基础 .... 1", "乙…………错误§", "乱…………转翻", "§2.1 示例 .... 4"]
        draft = self.recognize(upright, classified)
        self.assertEqual([entry.title for entry in draft.entries],
                         ["目录", "第一章 基础", "1.1 定义", "第二章 应用", "2.1 示例"])
        self.assertEqual([entry.pdf_page for entry in draft.entries], [1, 2, 3, 4, 5])

    def test_upside_down_scans_keep_better_angle_corrected_result(self):
        classified = ["Contents", "第一章 基础 .... 1", "§1.1 定义 .... 2", "第二章 应用 .... 3", "§2.1 示例 .... 4"]
        upright = ["错误", "乱……第", "己……§", "错误", "错误"]
        draft = self.recognize(upright, classified)
        self.assertEqual([entry.title for entry in draft.entries],
                         ["目录", "第一章 基础", "1.1 定义", "第二章 应用", "2.1 示例"])

    def test_normal_ocr_with_equal_alternatives_keeps_existing_mapping_and_titles(self):
        rows = ["Contents", "第一章 基础 .... 1", "§1.1 定义 .... 2"]
        draft = self.recognize(rows, rows)
        self.assertEqual(draft.mapping, {"offset": 1})
        self.assertEqual([entry.title for entry in draft.entries], ["目录", "第一章 基础", "1.1 定义"])

    def refine(self, old, new, score=.999):
        image = np.full((100, 300, 3), 255, dtype=np.uint8)
        image[50:70, 25:105] = 0
        for x in range(125, 260, 10):
            image[60:62, x:x + 2] = 0
        pix = pymupdf.Pixmap(pymupdf.csRGB, 300, 100, image.tobytes(), False)
        line = generation._Line(old + " ...... 12", 20, 60, 20, .8)
        engine = SimpleNamespace(
            cls_and_rotate=lambda crops: (crops, None),
            recognize_txt=lambda crops: SimpleNamespace(txts=[new], scores=[score]),
        )
        generation._refine_ocr_titles(pix, [line], engine, None)
        return line

    def test_short_title_retry_recovers_missing_character_and_keeps_page(self):
        line = self.refine("第章基础", "第一章 基础")
        self.assertEqual(generation._parse([line])[0][:2], ("第一章基础", 12))
        self.assertEqual(line.score, .8, "Title-only recognition must retain the page confidence cap")

    def test_short_title_retry_rejects_truncation_despite_high_confidence(self):
        line = self.refine("部分习题答案和提示", "部分习题答案和提")
        self.assertEqual(generation._parse([line])[0][:2], ("部分习题答案和提示", 12))

    def test_short_title_retry_does_not_change_existing_numbers(self):
        for old, new in (("§1.1 基础", "§1.11 基础"), ("第一章 基础", "第二章 基础"),
                         ("1.11 基础", "11.1 基础"), ("1.2.3 基础", "12.3 基础"),
                         ("第一节 基础", "第二节 基础"), ("§1 基础知识", "§2 基础知识"),
                         ("Section 1.11 Basics", "Section 11.1 Basics")):
            with self.subTest(old=old):
                line = self.refine(old, new)
                self.assertEqual(generation._parse([line])[0][0], generation._clean(old))

    def test_short_title_retry_allows_spacing_and_dot_normalization(self):
        line = self.refine("§1．1 基础", "1.1 基础知识")
        self.assertEqual(generation._parse([line])[0][:2], ("1.1 基础知识", 12))
        self.assertEqual(line.score, .8)

    def test_short_title_retry_rejects_numeric_unrelated_and_low_score_text(self):
        for new, score in (("12", .999), ("完全无关的其他标题", .999), ("第一章 基础", .7)):
            with self.subTest(new=new):
                line = self.refine("第章基础", new, score)
                self.assertEqual(generation._parse([line])[0][0], "第章基础")

    def test_cancellation_during_orientation_retry_restores_engine_state(self):
        cancel = threading.Event()

        class Engine:
            use_cls = True

            def __call__(self, image, use_cls=None):
                self.use_cls = use_cls
                cancel.set()
                return output(["Contents", "第一章 基础 .... 1", "§1.1 定义 .... 2"])

        engine = Engine()
        with patch.object(generation, "_engine", return_value=engine):
            with self.assertRaisesRegex(generation.BookmarkError, "取消"):
                generation.generate_toc(self.pdf, toc_start=1, toc_end=1, offset=1, cancel=cancel)
        self.assertTrue(engine.use_cls)
        self.assertEqual(self.pdf.read_bytes(), self.original)

    def test_failure_during_orientation_retry_restores_engine_state(self):
        class Engine:
            use_cls = True

            def __call__(self, image, use_cls=None):
                self.use_cls = use_cls
                if not use_cls:
                    raise RuntimeError("OCR retry failed")
                return output(["Contents", "第一章 基础 .... 1", "§1.1 定义 .... 2"])

        engine = Engine()
        with pymupdf.open(self.pdf) as document:
            pix = document[0].get_pixmap()
        with patch.object(generation, "_engine", return_value=engine):
            with self.assertRaisesRegex(RuntimeError, "OCR retry failed"):
                generation._ocr_lines(pix)
        self.assertTrue(engine.use_cls)
        self.assertEqual(self.pdf.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
