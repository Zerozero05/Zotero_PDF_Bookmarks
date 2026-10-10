"""Read-only TOC recognition and explicitly reviewed JSON drafts.

OCR is local and loaded only for pages without useful text.  Recognition is a
draft: page evidence and recognition confidence never replace user confirmation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile

import pymupdf

from bookmarks_core import BookmarkError, inspect_pdf


@dataclass
class DraftEntry:
    level: int
    title: str
    printed_page: int | None
    pdf_page: int | None = None
    confidence: str = "待确认"
    note: str = ""
    confirmed: bool = False


@dataclass
class Draft:
    pdf_path: Path
    page_count: int
    entries: list[DraftEntry]
    toc_pages: list[int]
    mapping: dict | None
    method: str
    notes: list[str]


@dataclass
class _Line:
    text: str
    x: float
    y: float
    height: float
    score: float
    numbers: list[tuple[int, float]] = field(default_factory=list)


_PART = re.compile(r"^(?:第\s*[\d一二三四五六七八九十百零〇]+\s*[篇部卷]|part\s+(?:[\divxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b)", re.I)
_CHAPTER = re.compile(r"^(?:第\s*[\d一二三四五六七八九十百零〇]+\s*[章篇部卷]|(?:chapter|part)\s+(?:[\divxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b)", re.I)
_FRONT = re.compile(r"^(?:前言|序言|自序|序|致谢|出版说明|preface|foreword|acknowledg)", re.I)
_ROOT = re.compile(r"^(?:附录|参考文献|索引|习题解答|appendix|references|bibliography|index|(?:外国)?(?:数学家)?译名对照表$|关键词索引$|(?:部分)?习题答案(?:和提示)?$)", re.I)
_LEVEL_ROOT = re.compile(r"^(?:符号表|注释|prologue\b|notes\s+and\s+comments|list\s+of\s+(?:special\s+)?symbols)", re.I)
_PAGE = re.compile(r"^(.*?)(?:\s|[.．·…⋯_—-])+(\d{1,5}|[ivxlcdmIVXLCDM]{1,10})\s*$")
_NUM = re.compile(r"^[\s\-—–·.]*([0-9]{1,5})[\s\-—–·.]*$")
_OCR = None


def _integer(value, label, *, positive=False):
    if type(value) is not int or (positive and value < 1):
        raise BookmarkError(f"{label} 必须是{'正' if positive else ''}整数。")
    return value


def _cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise BookmarkError("已取消识别；原 PDF 未修改。")


def _mapping(mapping, page_count):
    if not isinstance(mapping, dict) or set(mapping) not in ({"offset"}, {"segments"}):
        raise BookmarkError("页码映射必须且只能包含 offset 或 segments。")
    if "offset" in mapping:
        return {"offset": _integer(mapping["offset"], "offset")}
    segments = mapping["segments"]
    if not isinstance(segments, list) or not segments:
        raise BookmarkError("segments 必须是非空数组。")
    clean = []
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {"printed_start", "printed_end", "pdf_start"}:
            raise BookmarkError("每个分段必须提供 printed_start、printed_end 和 pdf_start。")
        item = {key: _integer(value, key, positive=True) for key, value in segment.items()}
        if item["printed_end"] < item["printed_start"]:
            raise BookmarkError("分段的终止印刷页码不能小于起始页码。")
        if item["pdf_start"] + item["printed_end"] - item["printed_start"] > page_count:
            raise BookmarkError("分段映射超出 PDF 页数。")
        clean.append(item)
    clean.sort(key=lambda item: item["printed_start"])
    if any(b["printed_start"] <= a["printed_end"] for a, b in zip(clean, clean[1:])):
        raise BookmarkError("分段的印刷页码范围不能重叠。")
    return {"segments": clean}


def _mapped(printed, mapping):
    if mapping is None:
        return None
    if "offset" in mapping:
        return printed + mapping["offset"]
    for segment in mapping["segments"]:
        if segment["printed_start"] <= printed <= segment["printed_end"]:
            return segment["pdf_start"] + printed - segment["printed_start"]
    return None


def apply_mapping(draft, mapping):
    """Apply an explicit mapping without guessing uncovered or invalid targets."""
    clean = _mapping(mapping, draft.page_count)
    targets = []
    for entry in draft.entries:
        if entry.printed_page is None:
            targets.append(None)
        else:
            printed = _integer(entry.printed_page, "印刷页码", positive=True)
            target = _mapped(printed, clean)
            targets.append(target if target is not None and 1 <= target <= draft.page_count else None)
    draft.mapping = clean
    for entry, target in zip(draft.entries, targets):
        if entry.printed_page is not None:
            entry.pdf_page = target
            entry.confirmed = False
            entry.confidence = "待确认"
            entry.note = "已按指定映射计算，请核对目标页。" if target else "此印刷页码未被映射覆盖或目标超出 PDF；请校正。"


def to_toc(draft):
    """Serialize only confirmed entries into the existing, strict TOC schema."""
    if not draft.entries:
        raise BookmarkError("目录为空，请先识别或添加条目。")
    mapping = _mapping(draft.mapping, draft.page_count) if draft.mapping is not None else None
    roots, parents = [], []
    previous = 0
    for index, entry in enumerate(draft.entries, 1):
        level = _integer(entry.level, f"第 {index} 项层级", positive=True)
        if level > previous + 1:
            raise BookmarkError(f"第 {index} 项层级跳级；首项须为 1，后续最多增加一级。")
        if not isinstance(entry.title, str) or not entry.title.strip() or "\x00" in entry.title:
            raise BookmarkError(f"第 {index} 项标题不能为空或包含空字符。")
        target = _integer(entry.pdf_page, f"第 {index} 项 PDF 页", positive=True)
        if target > draft.page_count:
            raise BookmarkError(f"第 {index} 项目标超出 PDF 的 {draft.page_count} 页。")
        if entry.confirmed is not True:
            raise BookmarkError(f"第 {index} 项“{entry.title}”尚未确认，请校对后确认。")
        printed = entry.printed_page
        if printed is not None:
            _integer(printed, f"第 {index} 项印刷页码", positive=True)
        node = {"title": entry.title.strip()}
        if printed is not None and mapping is not None and _mapped(printed, mapping) == target:
            node["page"] = printed
        else:
            node["pdf_page"] = target
        parents = parents[:level - 1]
        if level == 1:
            roots.append(node)
        else:
            parents[-1].setdefault("children", []).append(node)
        parents.append(node)
        previous = level
    result = {"version": 1, "bookmarks": roots}
    if mapping is not None:
        result["mapping"] = mapping
    return result


def save_toc(draft, path):
    """Validate a temporary JSON, then atomically save it; never write the PDF."""
    path = Path(path).expanduser().absolute()
    if path.suffix.lower() != ".json" or path.name.casefold() == "jasminum-outline.json":
        raise BookmarkError("请选择 .json 目录文件；不能覆盖 PDF 或茉莉花缓存。")
    if path.is_symlink() or path.resolve() == draft.pdf_path.resolve():
        raise BookmarkError("目录输出不能是符号链接或原 PDF。")
    if path.exists() and path.samefile(draft.pdf_path):
        raise BookmarkError("目录输出与原 PDF 指向同一文件。")
    if path.is_file():
        with path.open("rb") as stream:
            if stream.read(1024).lstrip().startswith(b"%PDF-"):
                raise BookmarkError("输出文件实际包含 PDF；不能覆盖，请选择新的 JSON 文件名。")
    data = to_toc(draft)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", prefix=".toc-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        plan = inspect_pdf(draft.pdf_path, temporary)
        if plan.page_count != draft.page_count:
            raise BookmarkError("PDF 页数已变化，请重新识别。")
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_draft(pdf_path, toc_path):
    plan = inspect_pdf(pdf_path, toc_path)
    with Path(toc_path).open(encoding="utf-8-sig") as stream:
        data = json.load(stream)
    entries = [DraftEntry(row["level"], row["title"], row["printed_page"], row["pdf_page"], "已导入", "来自已校验的 JSON。", True) for row in plan.preview]
    return Draft(plan.pdf_path, plan.page_count, entries, [], data.get("mapping"), "导入 JSON", [])


def _engine():
    global _OCR
    if _OCR is None:
        try:
            import rapidocr
            models = Path(rapidocr.__file__).parent / "models"
            files = {"Det": "PP-OCRv6_det_small.onnx", "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "Rec": "PP-OCRv6_rec_small.onnx"}
            params = {"Global.log_level": "error", "EngineConfig.onnxruntime.intra_op_num_threads": 2, "EngineConfig.onnxruntime.inter_op_num_threads": 1}
            for task, name in files.items():
                model = models / name
                if not model.is_file():
                    raise BookmarkError(f"本地 OCR 模型缺失：{name}。请使用完整安装包；不会联网下载。")
                params[f"{task}.model_path"] = str(model)
            _OCR = rapidocr.RapidOCR(params=params)
        except BookmarkError:
            raise
        except Exception as error:
            raise BookmarkError(f"无法启动本地 OCR，请检查完整安装包：{error}") from error
    return _OCR


def _group_boxes(boxes):
    """Join title and a separate right-hand page box on the same baseline."""
    rows = []
    for x0, y0, x1, y1, text, score in sorted(boxes, key=lambda box: ((box[1] + box[3]) / 2, box[0])):
        middle, height = (y0 + y1) / 2, max(y1 - y0, 1)
        match = next((row for row in reversed(rows[-3:]) if abs(row[0] - middle) <= max(row[1], height) * .48), None)
        if match is None:
            rows.append([middle, height, [(x0, text, score)]])
        else:
            match[2].append((x0, text, score))
            match[1] = max(match[1], height)
    return [_Line(" ".join(part[1] for part in sorted(parts)), min(part[0] for part in parts), middle, height, min(part[2] for part in parts), [(int(match.group(1)), part[0]) for part in parts if (match := _NUM.match(part[1]))]) for middle, height, parts in rows]


def _clean(text):
    text = re.sub(r"[.．·…⋯_](?:\s*[.．·…⋯_])+", " ", text)
    text = re.sub(r"[.．·…⋯_]\s*$", "", text).strip()
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text)


def _normal(text):
    return re.sub(r"[^\w\u3400-\u9fff]", "", text).casefold()


def _heading_number(text):
    """Read the chapter/section numerals already supported by recognition."""
    if text.isdecimal():
        return int(text)
    digits = dict(zip("零〇一二三四五六七八九", (0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9)))
    if all(char in digits for char in text):
        return int("".join(str(digits[char]) for char in text))
    if all(char in digits or char in "十百" for char in text):
        total, value = 0, 0
        for char in text:
            if char in "十百":
                total += (value or 1) * {"十": 10, "百": 100}[char]
                value = 0
            else:
                value = digits[char]
        return total + value
    words = "one two three four five six seven eight nine ten".split()
    if text.casefold() in words:
        return words.index(text.casefold()) + 1
    if not re.fullmatch(r"[ivxlcdm]+", text, re.I):
        return None
    total, previous = 0, 0
    for char in reversed(text.upper()):
        value = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}[char]
        total += -value if value < previous else value
        previous = max(previous, value)
    return total


def _format_generated_titles(draft):
    """Format automatic titles after page evidence, leaving imported titles alone."""
    numeral = r"[\d一二三四五六七八九十百零〇]+"
    prefix = re.compile(rf"^(?:第\s*{numeral}\s*(?:小节|章|节|篇|部|卷)|"
                        rf"(?:chapter|part)\s+(?:[\divxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b|"
                        rf"(?:section\s+|sec\.\s*)\d+(?:\s*[.．]\s*\d+)*(?![\d.．])|"
                        rf"§?\s*\d+(?:\s*[.．]\s*\d+)+(?![\d.．])|§\s*\d+(?![\d.．])|\d+(?=\s|$)|"
                        rf"[一二三四五六七八九十百]+[、．]|[（(][一二三四五六七八九十百]+[）)])", re.I)

    def section_number(title, chapter):
        numbered = re.sub(r"^(?:section\s+|sec\.\s*)", "", title, flags=re.I)
        match = re.match(r"^(\d+)(?:[.．](\d+))?(?=\s|$)", numbered)
        if match:
            return int(match[2]) if match[2] and int(match[1]) == chapter else (int(match[1]) if not match[2] else None)
        match = re.match(rf"^(?:第\s*({numeral})\s*节|([一二三四五六七八九十百]+)[、．])", title)
        return _heading_number(match[1] or match[2]) if match else None

    for entry in draft.entries:
        if match := prefix.match(entry.title):
            label = match[0].lstrip("§").strip()
            if re.match(r"^(?:chapter|part|section)\b|^sec\.", label, re.I):
                label = re.sub(r"\s+", " ", label)
            else:
                label = re.sub(r"\s+", "", label)
            entry.title = (label + " " + entry.title[match.end():].strip()).rstrip()

    chapter, chapter_level, sequence, reserved = None, 0, 0, set()
    for index, entry in enumerate(draft.entries):
        match = re.match(rf"^第\s*({numeral})\s*章", entry.title)
        english = _CHAPTER.match(entry.title) if re.match(r"^chapter\b", entry.title, re.I) else None
        if match or english:
            chapter = _heading_number(match[1] if match else english[0].split()[1])
            chapter_level, sequence, reserved = entry.level, 0, set()
            # Reserve existing numbers even when they appear after a missing one.
            for following in draft.entries[index + 1:]:
                if following.level <= chapter_level:
                    break
                if following.level == chapter_level + 1:
                    number = section_number(following.title, chapter)
                    if number is not None:
                        reserved.add(number)
        elif entry.level <= chapter_level or _PART.match(entry.title) or _ROOT.match(entry.title) or _FRONT.match(entry.title) or _LEVEL_ROOT.match(entry.title):
            chapter = None
        elif chapter is not None and entry.level == chapter_level + 1:
            number = section_number(entry.title, chapter)
            if number is not None:
                sequence = max(sequence, number)
            elif not prefix.match(entry.title) and _level(entry.title) == 1:
                sequence += 1
                while sequence in reserved:
                    sequence += 1
                entry.title = f"{chapter}.{sequence} {entry.title}"
                entry.note += " 根据章内顺序补充节编号，请核对。"
    return draft


def _level(title):
    if _CHAPTER.match(title) or _FRONT.match(title) or _ROOT.match(title) or _LEVEL_ROOT.match(title):
        return 1
    if re.match(r"^(?:subsection\b|subsec\.|第\s*[\d一二三四五六七八九十百]+\s*小节|[（(][一二三四五六七八九十百]+[）)])", title, re.I):
        return 3
    numbered = re.match(r"^(?:[§S]|section\s+|sec\.\s*)?\s*(\d+(?:\s*[.．]\s*\d+)+)", title, re.I)
    if numbered:
        return min(len(re.split(r"[.．]", numbered.group(1))), 5)
    appendix_number = re.match(r"^[A-Z]((?:[.．]\s*\d+)+)(?:\s|[.．、:]|$)", title)
    if appendix_number:
        return min(appendix_number.group(1).count(".") + appendix_number.group(1).count("．") + 1, 5)
    if re.match(r"^(?:§\s*\d+|第\s*[\d一二三四五六七八九十百]+\s*节|section\b|sec\.|[一二三四五六七八九十百]+[、．])", title, re.I):
        return 2
    return 1


def _hierarchy(parts, layout):
    """Use heading roles across pages; indentation is only a reviewable hint."""
    # Normalize indentation by page width, and compare only siblings on one page
    # within one chapter: facing-page margins and OCR scale must not add levels.
    groups, bases, anchor = [], {}, 0
    for (title, _, _, _), (page, x) in zip(parts, layout):
        if _CHAPTER.match(title) or _FRONT.match(title) or _ROOT.match(title) or _LEVEL_ROOT.match(title):
            anchor += 1
        key = (page, anchor)
        groups.append(key)
        if not (_CHAPTER.match(title) or _FRONT.match(title) or _ROOT.match(title) or _LEVEL_ROOT.match(title)):
            bases[key] = min(bases.get(key, x), x)
    result, previous, part, parent = [], 0, False, None
    for (title, _, _, _), (_, x), group in zip(parts, layout, groups):
        local, note = _level(title), ""
        if _PART.match(title):
            level, part, parent = 1, True, None
        elif _CHAPTER.match(title):
            level = 2 if part else 1
            parent = level
        elif _FRONT.match(title) or _ROOT.match(title) or _LEVEL_ROOT.match(title):
            level, part = 1, False
            parent = 1 if re.match(r"^(?:附录|appendix\b)", title, re.I) else None
        elif local > 1:
            level = local + (parent - 1 if parent is not None else int(part))
        elif parent is not None:
            level = parent + 1
            note = "根据最近的章/附录推断为子目录，请核对层级。"
            if x - bases.get(group, x) >= .035:
                level += 1
                note = "根据章内较深缩进推断为小节，请核对层级。"
        elif part:
            level = 2
            note = "根据最近的篇/部推断为子目录，请核对层级。"
        else:
            level = 1
        level = min(level, 5)
        if level > previous + 1:
            level = previous + 1
            note = "未识别到完整上级标题，已避免层级跳级；请核对层级。"
        result.append((level, note))
        previous = level
    return result


def _parse(lines, *, layout=None):
    parsed = []
    pending = ""
    pending_x = None
    for line in lines:
        raw = line.text.strip()
        match = _PAGE.match(raw)
        if not match:
            if _CHAPTER.match(raw) or re.match(r"^[§S]?\s*\d+[.．]\d+", raw):
                pending = _clean(raw)
                pending_x = line.x
            continue
        title = _clean(match.group(1))
        if not title or _normal(title) in {"目录", "contents", "tableofcontents"} or _NUM.match(title):
            continue
        x = line.x
        if pending and not (_CHAPTER.match(title) or re.match(r"^[§S]?\s*\d+[.．]\d+", title)):
            title = pending + " " + title
            x = pending_x
        pending = ""
        page_text = match.group(2)
        printed = int(page_text) if page_text.isdecimal() and int(page_text) > 0 else None
        parsed.append((title, printed, line.score, page_text))
        if layout is not None:
            layout.append(x)
    return parsed


def _looks_toc(lines, parsed):
    header = any(_normal(line.text) in {"目录", "目", "contents", "tableofcontents"} for line in lines)
    numbered = sum(bool(_CHAPTER.match(title) or re.match(r"^[§S]?\s*\d+[.．]\d+", title)) for title, _, _, _ in parsed)
    return (header and len(parsed) >= 2) or (len(parsed) >= 5 and numbered >= 3)


def _title_crop(image, line):
    """Find a repeated pixel dot leader without guessing the title width."""
    import cv2

    height = line.height
    crop = image[max(0, int(line.y - height / 2) - 5):int(line.y + height / 2) + 5,
                 max(0, int(line.x) - 5):]
    if not crop.size:
        return None
    _, ink = cv2.threshold(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), 0, 255,
                          cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    _, _, stats, centers = cv2.connectedComponentsWithStats(ink, 8)
    dots = sorted([(stat, center) for stat, center in zip(stats[1:], centers[1:])
                   if stat[2] <= height * .2 and stat[3] <= height * .2
                   and 2 <= stat[4] <= height * height * .04], key=lambda item: item[0][0])
    runs, run = [], []
    for dot in dots:
        if run and not (abs(dot[1][1] - run[-1][1][1]) < height * .15
                        and 0 < dot[1][0] - run[-1][1][0] < height * .7):
            if len(run) >= 6:
                runs.append(run)
            run = []
        run.append(dot)
    if len(run) >= 6:
        runs.append(run)
    end = int(max(runs, key=len)[0][0][0]) - 1 if runs else 0
    return crop[:, :end] if end > height else None


def _refine_ocr_titles(pix, lines, engine, cancel):
    """Re-read image-backed short titles, retaining the original page numbers."""
    from difflib import SequenceMatcher
    import numpy as np

    def label(text):
        match = _CHAPTER.match(text) or re.match(
            r"^(?:[§S]?\s*\d+(?:\s*[.．]\s*\d+)*|"
            r"第\s*[\d一二三四五六七八九十百零〇]+\s*(?:小节|节)|"
            r"(?:section|subsection|sec\.)\s*\d+(?:\s*[.．]\s*\d+)*|"
            r"[一二三四五六七八九十百]+[、．]|[（(][一二三四五六七八九十百]+[）)])", text, re.I)
        if match:
            return re.sub(r"^[§S](?=\s*\d)|\s+", "", match[0]).replace("．", ".").casefold()

    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    candidates = []
    for line in lines:
        _cancelled(cancel)
        parsed = _parse([line])
        if parsed and (crop := _title_crop(image, line)) is not None:
            candidates.append((line, parsed[0][0], _PAGE.match(line.text)[2], crop))
    if not candidates:
        return
    _cancelled(cancel)
    crops, _ = engine.cls_and_rotate([item[3] for item in candidates])
    _cancelled(cancel)
    result = engine.recognize_txt(crops)
    _cancelled(cancel)
    if result.txts is None or result.scores is None:
        return
    for (line, old_title, page, _), text, score in zip(candidates, result.txts, result.scores):
        title = _clean(text)
        old_key, key = _normal(old_title), _normal(title)
        old_label = label(old_title)
        if old_label is not None and label(title) != old_label:
            continue
        if (title and not _NUM.match(title) and len(key) >= len(old_key)
                and score >= max(.92, line.score) and SequenceMatcher(None, old_key, key).ratio() >= .7):
            line.text = f"{title} {page}"


def _ocr_lines(pix, cancel=None):
    """Compare TOC orientations; long leaders can fool the angle classifier."""
    engine = _engine()
    original_cls = engine.use_cls
    image = pix.tobytes("png")

    def recognize(use_cls):
        _cancelled(cancel)
        output = engine(image, use_cls=use_cls)
        if output.boxes is None or output.txts is None:
            return []
        return _group_boxes([
            (float(min(box[:, 0])), float(min(box[:, 1])), float(max(box[:, 0])),
             float(max(box[:, 1])), str(text), float(score))
            for box, text, score in zip(output.boxes, output.txts, output.scores)
        ])

    def quality(lines):
        parsed = _parse(lines)
        return len(parsed), sum(score for _, _, score, _ in parsed)

    try:
        lines = recognize(True)
        leaders = sum(bool(re.search(r"[.．·…⋯](?:\s*[.．·…⋯]){3}", line.text)) for line in lines)
        if _looks_toc(lines, _parse(lines)) or leaders >= 2:
            upright = recognize(False)
            if quality(upright) > quality(lines):
                lines = upright
            _refine_ocr_titles(pix, lines, engine, cancel)
        return lines
    finally:
        # RapidOCR call options persist on its singleton; keep later pages safe.
        engine.use_cls = original_cls


def generate_toc(pdf_path, *, toc_start=None, toc_end=None, offset=None, prefer_existing=False, progress=None, cancel=None):
    """Recognize a reviewable draft without changing the PDF or its caches."""
    path = Path(pdf_path).expanduser().absolute()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise BookmarkError("请选择可读取的 PDF 文件。")
    report = progress if progress is not None else lambda message: None
    _cancelled(cancel)
    with pymupdf.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass or doc.page_count < 1:
            raise BookmarkError("PDF 为空、无法读取或需要密码。")
        count = doc.page_count
        for value, name in ((toc_start, "目录起始页"), (toc_end, "目录结束页")):
            if value is not None and not 1 <= _integer(value, name, positive=True) <= count:
                raise BookmarkError(f"{name} 必须在 PDF 的 1–{count} 页之间。")
        if toc_end is not None and toc_start is None:
            raise BookmarkError("指定目录结束页时须同时指定起始页。")
        if toc_start is not None and toc_end is not None and toc_end < toc_start:
            raise BookmarkError("目录结束页不能早于起始页。")
        if offset is not None:
            _integer(offset, "固定偏移")
        if prefer_existing:
            outline = doc.get_toc()
            if outline:
                entries = [DraftEntry(level, title, None, page if 1 <= page <= count else None, "待确认", "来自已有 PDF 书签，请检查完整性。") for level, title, page in outline]
                return Draft(path, count, entries, [], None, "已有 PDF 书签", ["复用了已有书签；可能不完整，仍需确认。"])

        cache, methods = {}, set()

        def read(number):
            _cancelled(cancel)
            if number not in cache:
                report(f"读取 PDF 第 {number}/{count} 页……")
                page = doc[number - 1]
                words = page.get_text("words")
                useful = "".join(str(word[4]) for word in words)
                if len(useful) >= 8 and useful.count("�") < len(useful) * .1:
                    boxes = [(a, b, c, d, str(text), 1.0) for a, b, c, d, text, *_ in words]
                    lines = _group_boxes(boxes)
                    width, height = page.rect.width, page.rect.height
                    methods.add("文字提取")
                else:
                    report(f"本地 OCR：PDF 第 {number}/{count} 页……")
                    scale = min(4.0, 1900 / max(page.rect.width, page.rect.height))
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                    lines = _ocr_lines(pix, cancel)
                    width, height = pix.width, pix.height
                    methods.add("本地 OCR")
                _cancelled(cancel)
                cache[number] = (lines, width, height)
            return cache[number]

        toc_pages, parts, layout = [], [], []
        first = toc_start
        if first is None:
            for number in range(1, min(count, 25) + 1):
                lines, _, _ = read(number)
                parsed = _parse(lines)
                if _looks_toc(lines, parsed):
                    first = number
                    break
        if first is None:
            raise BookmarkError("前 25 页未找到可识别目录。请指定目录起止 PDF 页后再试，或导入已有 toc.json。")
        end = toc_end if toc_end is not None else min(count, first + 14)
        for number in range(first, end + 1):
            lines, width, _ = read(number)
            page_layout = []
            parsed = _parse(lines, layout=page_layout)
            numbered = sum(bool(_CHAPTER.match(title) or re.match(r"^[§S]?\s*\d+[.．]\d+", title)) for title, _, _, _ in parsed)
            continuation = parsed and (numbered >= 2 or any(_ROOT.match(title) for title, _, _, _ in parsed) or _looks_toc(lines, parsed))
            if toc_end is None and number > first and not continuation:
                break
            toc_pages.append(number)
            parts.extend(parsed)
            layout.extend((number, x / width) for x in page_layout)
        if not parts:
            raise BookmarkError("指定页未识别出标题和页码，请检查目录页范围或使用手工编辑。")
        entries = [DraftEntry(1, "目录", None, first, "高", "目录起始 PDF 页。")]
        seen, notes = set(), ["自动识别结果均未确认；请检查标题、层级与目标页后再导出或写入。"]
        for (title, printed, score, page_text), (level, hierarchy_note) in zip(parts, _hierarchy(parts, layout)):
            key = (title, printed)
            if key in seen:
                continue
            seen.add(key)
            entry = DraftEntry(level, title, printed, confidence="中" if score >= .92 else "低", note="文字提取标题，需检查。" if score == 1.0 else f"OCR 标题分数 {score:.2f}，需检查错字。")
            if hierarchy_note:
                entry.note += " " + hierarchy_note
            if printed is None:
                entry.note += f" 非阿拉伯或无效印刷页码 {page_text!r}，请指定实际 PDF 页。"
            if _FRONT.match(title):
                entry.printed_page = None
                entry.note += f" 前置部分的 {page_text} 可能另起页码，不套用正文偏移。"
                target = next((number for number in range(1, first) if any(_normal(line.text) == _normal(title) for line in read(number)[0])), None)
                entry.pdf_page = target
            entries.append(entry)
        draft = Draft(path, count, entries, toc_pages, None, " + ".join(sorted(methods)), notes)

        def generated_mapping(mapping):
            evidence = [(entry.confidence, entry.note) for entry in entries]
            apply_mapping(draft, mapping)
            for entry, (confidence, note) in zip(entries, evidence):
                if entry.printed_page is not None:
                    entry.confidence = confidence if entry.pdf_page is not None else "待确认"
                    entry.note = note + " " + entry.note

        if offset is not None:
            generated_mapping({"offset": offset})
            notes.append("使用用户指定固定偏移；仍须检查中途缺页或插页。")
            return _format_generated_titles(draft)

        def footer(number):
            lines, width, height = read(number)
            numbers = []
            for line in lines:
                if line.y < height * .12 or line.y > height * .88:
                    if match := _NUM.match(line.text):
                        numbers.append(int(match.group(1)))
                    else:
                        numbers.extend(value for value, x in line.numbers if x < width * .2 or x > width * .7)
            return list(dict.fromkeys(numbers))

        votes = []
        for number in range(toc_pages[-1] + 1, min(count, toc_pages[-1] + 7) + 1):
            values = footer(number)
            if len(values) == 1:
                votes.append(number - values[0])
        counts = Counter(votes)
        candidates = counts.most_common()
        if not candidates or candidates[0][1] < 2 or (len(candidates) > 1 and candidates[1][1] == candidates[0][1]):
            notes.append("未找到至少两页一致的正文页码证据，请输入固定偏移或分段映射。")
            return _format_generated_titles(draft)
        suggested = candidates[0][0]
        printed_entries = [entry for entry in entries if entry.printed_page is not None]
        if not printed_entries:
            notes.append("目录未包含可用于正文映射的阿拉伯页码，请逐项指定实际 PDF 页。")
            return _format_generated_titles(draft)
        sample_indices = sorted({round(index * (len(printed_entries) - 1) / 5) for index in range(6)})
        verified, conflicts = [], []
        for index in sample_indices:
            entry = printed_entries[index]
            target = entry.printed_page + suggested
            if not 1 <= target <= count:
                conflicts.append(f"{entry.title} 推算目标超出范围")
                continue
            lines = read(target)[0]
            numbers = footer(target)
            title_key = _normal(entry.title)
            heading = any(title_key and (title_key in _normal(line.text) or _normal(line.text) in title_key and len(_normal(line.text)) >= 5) for line in lines[:12])
            if entry.printed_page in numbers or heading:
                verified.append((entry, target))
            elif numbers and entry.printed_page not in numbers:
                conflicts.append(f"印刷 {entry.printed_page} 页的预计 PDF {target} 页，页眉/脚为 {numbers}")
        if conflicts:
            notes.append("发现页码不一致，可能有缺页、插页或页码重启；请使用分段映射。" + "；".join(conflicts))
            for entry, target in verified:
                entry.pdf_page = target
                entry.note += " 此目标有页码或标题证据，整体映射仍待校正。"
            return _format_generated_titles(draft)
        generated_mapping({"offset": suggested})
        for entry, target in verified:
            if entry.confidence != "低":
                entry.confidence = "高"
            entry.note += " 目标页有独立页码或标题证据；标题仍需校对。"
        notes.append(f"建议固定偏移 {suggested:+d}：正文开头 {counts[suggested]} 页一致，跨目录抽样 {len(verified)} 项有支持证据。未检查每一页。")
        draft.method = " + ".join(sorted(methods))
        return _format_generated_titles(draft)
