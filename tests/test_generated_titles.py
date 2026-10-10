"""Generated title formatting uses temporary PDFs, never user documents."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

import toc_generation as generation
from toc_generation import generate_toc, load_draft, save_toc, to_toc


class GeneratedTitleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pdf-bookmarks-titles-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.pdf = self.folder / "目录 标题.pdf"

    def recognize(self, pages, *, offset=True, footers=True):
        titles = [title for rows in pages for title, _ in rows]
        with pymupdf.open() as document:
            printed = 0
            for rows in pages:
                page = document.new_page(width=600, height=800)
                page.insert_text((30, 35), "Contents")
                for index, (title, x) in enumerate(rows, 1):
                    printed += 1
                    font = "china-s" if any(ord(letter) > 127 for letter in title) else "helv"
                    page.insert_text((x, 60 + index * 30), f"{title} .... {printed}", fontname=font)
            for index, title in enumerate(titles, 1):
                page = document.new_page(width=600, height=800)
                font = "china-s" if any(ord(letter) > 127 for letter in title) else "helv"
                page.insert_text((30, 50), title, fontname=font)
                page.insert_text((30, 100), "Original readable body text stays unchanged.")
                # 无编号短标题只提供标题证据，避免新增编号破坏页码识别。
                if footers and title != "迭代":
                    page.insert_text((300, 785), str(index))
            document.save(self.pdf)
        original = self.pdf.read_bytes()
        options = {"offset": len(pages)} if offset else {}
        with patch.object(generation, "_engine", side_effect=AssertionError("unexpected OCR")):
            draft = generate_toc(self.pdf, toc_start=1, toc_end=len(pages), **options)
        self.assertEqual(self.pdf.read_bytes(), original)
        self.assertFalse(any(entry.confirmed for entry in draft.entries))
        return draft

    def test_chapter_and_existing_number_spacing_and_section_sign(self):
        draft = self.recognize([[(title, 40) for title in (
            "第一章迭代与动力系统", "§1.1迭代", "1.2  动力系统",
            "1.2.1局部行为", "第一节基本概念", "§3 其他内容",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "第一章 迭代与动力系统", "1.1 迭代", "1.2 动力系统",
            "1.2.1 局部行为", "第一节 基本概念", "3 其他内容",
        ])
        self.assertEqual([entry.level for entry in draft.entries], [1, 1, 2, 2, 3, 2, 2])
        self.assertEqual([entry.pdf_page for entry in draft.entries], [1, 2, 3, 4, 5, 6, 7])

    def test_missing_direct_sections_use_actual_chapter_and_continue_across_pages(self):
        draft = self.recognize([
            [("第十章迭代与动力系统", 30), ("迭代", 60), ("局部行为", 90)],
            [("动力系统", 50), ("第十一章稳定性", 30), ("稳定性定义", 50)],
        ])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "第十章 迭代与动力系统", "10.1 迭代", "局部行为",
            "10.2 动力系统", "第十一章 稳定性", "11.1 稳定性定义",
        ])
        self.assertEqual([entry.level for entry in draft.entries[1:]], [1, 2, 3, 2, 1, 2])

    def test_mixed_numbered_sections_keep_numbers_without_new_duplicates(self):
        draft = self.recognize([[(title, 40) for title in (
            "第一章基础", "§1.1 已有编号", "无编号节", "1.4 已有第四节",
            "后续无编号节", "第二章补充", "无编号首项", "2.1 已有首节",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "第一章 基础", "1.1 已有编号", "1.2 无编号节", "1.4 已有第四节",
            "1.5 后续无编号节", "第二章 补充", "2.2 无编号首项", "2.1 已有首节",
        ])

    def test_parts_front_matter_and_appendices_do_not_get_chapter_section_numbers(self):
        draft = self.recognize([[(title, 40) for title in (
            "前言", "第一篇基础", "第3章动力系统", "迭代", "附录A公式",
            "补充材料", "参考文献", "索引",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "前言", "第一篇 基础", "第3章 动力系统", "3.1 迭代", "附录A公式",
            "补充材料", "参考文献", "索引",
        ])
        self.assertEqual([entry.level for entry in draft.entries[1:]], [1, 1, 2, 3, 1, 2, 1, 1])

    def test_english_chapter_and_unnumbered_titles_without_chapter(self):
        draft = self.recognize([[(title, 40) for title in (
            "Overview", "Another heading", "Chapter IV Foundations", "Iteration",
            "Chapter Five Stability", "Definition", "Index",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "Overview", "Another heading", "Chapter IV Foundations", "4.1 Iteration",
            "Chapter Five Stability", "5.1 Definition", "Index",
        ])

    def test_automatic_page_evidence_uses_original_unnumbered_title(self):
        draft = self.recognize([[(title, 40) for title in (
            "第一章基础", "已有定义", "迭代", "动力系统",
        )]], offset=False)
        self.assertEqual(draft.mapping, {"offset": 1})
        iteration = draft.entries[3]
        self.assertEqual(iteration.title, "1.2 迭代")
        self.assertEqual(iteration.pdf_page, 4)
        self.assertIn("独立页码或标题证据", iteration.note)

    def test_existing_chinese_and_english_section_numbers_advance_missing_sections(self):
        draft = self.recognize([[(title, 40) for title in (
            "第一章基础", "第一节基本概念", "无编号节", "三、已有第三节", "后续节",
            "Chapter 2 Foundations", "Section 1 Definition", "Missing section",
            "Sec. 3 Examples", "Another section",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "第一章 基础", "第一节 基本概念", "1.2 无编号节", "三、 已有第三节", "1.4 后续节",
            "Chapter 2 Foundations", "Section 1 Definition", "2.2 Missing section",
            "Sec. 3 Examples", "2.4 Another section",
        ])

    def test_generated_json_roundtrip_preserves_manual_edits(self):
        draft = self.recognize([[("第一章基础", 30), ("迭代", 60)]])
        for entry in draft.entries:
            entry.confirmed = True
        target = self.folder / "目录.toc.json"
        save_toc(draft, target)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["mapping"], {"offset": 1})
        self.assertEqual(data["bookmarks"][1], {
            "title": "第一章 基础", "page": 1,
            "children": [{"title": "1.1 迭代", "page": 2}],
        })
        imported = load_draft(self.pdf, target)
        imported.entries[2].title = "§1.1我的手工标题"
        self.assertEqual(to_toc(imported)["bookmarks"][1]["children"][0]["title"], "§1.1我的手工标题")

    def test_unknown_page_mapping_still_formats_titles_without_guessing_pages(self):
        draft = self.recognize([[("第一章基础", 30), ("迭代", 60)]], offset=False, footers=False)
        self.assertEqual([entry.title for entry in draft.entries], ["目录", "第一章 基础", "1.1 迭代"])
        self.assertIsNone(draft.mapping)
        self.assertEqual([entry.pdf_page for entry in draft.entries], [1, None, None])

    def test_uninterpretable_chapter_numbers_do_not_break_existing_recognition(self):
        draft = self.recognize([[(title, 40) for title in (
            "Chapter I2 Foundations", "Iteration", "第1十章基础", "无编号节",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "Chapter I2 Foundations", "Iteration", "第1十章 基础", "无编号节",
        ])

    def test_digit_leading_title_content_is_not_mistaken_for_section_number(self):
        draft = self.recognize([[(title, 40) for title in (
            "第一章基础", "3D动力系统", "2020年的研究", "100%概率", "迭代",
        )]])
        self.assertEqual([entry.title for entry in draft.entries[1:]], [
            "第一章 基础", "1.1 3D动力系统", "1.2 2020年的研究", "1.3 100%概率", "1.4 迭代",
        ])

    def test_reuse_existing_pdf_bookmarks_preserves_titles(self):
        self.recognize([[("第一章基础", 30), ("迭代", 60)]])
        outline_pdf = self.folder / "已有书签.pdf"
        with pymupdf.open(self.pdf) as document:
            document.set_toc([[1, "第一章基础", 2], [2, "§1.1迭代", 3]])
            document.save(outline_pdf)
        original = outline_pdf.read_bytes()
        draft = generate_toc(outline_pdf, prefer_existing=True)
        self.assertEqual([entry.title for entry in draft.entries], ["第一章基础", "§1.1迭代"])
        self.assertFalse(any(entry.confirmed for entry in draft.entries))
        self.assertEqual(outline_pdf.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
