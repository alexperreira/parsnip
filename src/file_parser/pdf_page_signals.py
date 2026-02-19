from pypdf import PdfReader


def _page_has_image(page):
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject")
        if not xobjects:
            return False
        for obj in xobjects.values():
            try:
                resolved = obj.get_object()
            except Exception:
                resolved = obj
            try:
                subtype = resolved.get("/Subtype")
            except Exception:
                continue
            if subtype == "/Image":
                return True
    except Exception:
        return False
    return False


def inspect_pdf_pages(pdf_obj):
    reader = PdfReader(pdf_obj, strict=False)
    page_signals = []
    for page_index, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_signals.append(
            {
                "page_index": page_index,
                "text": text,
                "text_char_count": len(text),
                "has_image": _page_has_image(page),
            }
        )
    return page_signals


def mixed_page_ocr_decision(
    text_char_count,
    has_image,
    text_page_min_chars,
    low_text_max_chars,
):
    text_chars = int(text_char_count or 0)
    if text_chars >= int(text_page_min_chars):
        return "skip_pdf_text", "text_page_threshold_met"
    if not bool(has_image):
        return "skip_no_image", "no_images_detected"
    if text_chars <= int(low_text_max_chars):
        return "ocr", "image_with_low_text"
    return "skip_pdf_text", "prefer_embedded_text"


def should_ocr_mixed_page(
    text_char_count,
    has_image,
    text_page_min_chars,
    low_text_max_chars,
):
    decision, _ = mixed_page_ocr_decision(
        text_char_count=text_char_count,
        has_image=has_image,
        text_page_min_chars=text_page_min_chars,
        low_text_max_chars=low_text_max_chars,
    )
    return decision == "ocr"
