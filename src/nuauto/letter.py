"""A cover letter as a PDF, for the one application it was written for (assist.py's `letter` command).

The company-site assistant drafts a letter for one job; the user approves it in the end list; this turns that text into
a plain one-column PDF the assistant uploads (NUworks' Apply popup and job sites take a cover letter as a file). It is
made in the run's own folder and removed when the session ends: a letter is never saved or reused.

No dependencies: a letter-size page, Helvetica 11 pt, one-inch margins, paragraphs wrapped by the font's own widths,
as many pages as the text needs. Text the font can't show (outside Windows-1252) becomes "?".
"""

PAGE_W, PAGE_H, MARGIN, SIZE, LEADING = 612, 792, 72, 11, 15  # points (1/72 inch)

# Helvetica's widths (1/1000 em) for the printable ASCII characters 32..126 (the standard PDF font metrics)
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
      556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
      833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500,
      556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
      334, 260, 334, 584]


def width(text):
    """The text's width in points at SIZE (characters outside ASCII count as an average letter)."""
    return sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text) * SIZE / 1000


def wrap(paragraph, room=PAGE_W - 2 * MARGIN):
    """One paragraph as lines that fit room points (a word longer than a line is cut)."""
    lines, line = [], ""
    for word in paragraph.split():
        while width(word) > room:  # a long link or number: cut where it fills the line
            n = len(word)
            while n > 1 and width(word[:n]) > room:
                n -= 1
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:n])
            word = word[n:]
        candidate = f"{line} {word}" if line else word
        if width(candidate) <= room:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _text(s):
    """A PDF string for s: Windows-1252 bytes (curly quotes and dashes included), with ( ) and \\ escaped."""
    raw = s.encode("cp1252", errors="replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def pages(paragraphs):
    """The lines of each page: paragraphs wrapped, a blank line between them, as many as fit a page."""
    lines = []
    for p in paragraphs:
        if lines:
            lines.append("")
        lines += wrap(p) or [""]
    per = int((PAGE_H - 2 * MARGIN) // LEADING)
    return [lines[i:i + per] for i in range(0, len(lines), per)] or [[]]


def pdf_bytes(paragraphs):
    """The whole PDF file for these paragraphs."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages(paragraphs):
        ops = [b"BT", f"/F1 {SIZE} Tf {LEADING} TL {MARGIN} {PAGE_H - MARGIN - SIZE} Td".encode()]
        for line in lines:
            ops.append(_text(line) + b" Tj T*")
        ops.append(b"ET")
        stream = b"\n".join(ops)
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {len(objects)} 0 R >>".encode())
        kids.append(len(objects))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def write(paragraphs, path):
    """Write the letter's PDF at path (mode 600: it is about you)."""
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pdf_bytes(paragraphs))
    return path
