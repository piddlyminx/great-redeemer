#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
import asyncio

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Image as RLImage, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from wos_redeem.captcha_solver import GiftCaptchaSolver


@dataclass
class Item:
    path: Path
    wrong: str
    suggested: str | None
    confidence: float | None


def parse_wrong_guess_from_filename(p: Path) -> str:
    """Extract the wrong guess from a filename produced by save_failure_captcha.

    Pattern: captcha_{ts}_fid{FID}_{guess}[_{reason}].ext
    Returns 'unknown' when not parsable.
    """
    stem = p.stem  # no extension
    parts = stem.split("_")
    try:
        fid_index = next(i for i, tok in enumerate(parts) if tok.startswith("fid"))
        wrong = parts[fid_index + 1]
        return wrong or "unknown"
    except Exception:
        return "unknown"


def iter_failure_images(root: Path, limit: int) -> List[Path]:
    imgs = [p for p in root.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    imgs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return imgs[:limit]


def build_pdf(items: List[Item], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(out_path), pagesize=landscape(A4), rightMargin=10*mm, leftMargin=10*mm, topMargin=10*mm, bottomMargin=10*mm)
    story: list = []
    styles = getSampleStyleSheet()
    story.append(Paragraph("CAPTCHA Failure Report", styles['Title']))
    story.append(Paragraph(datetime.now().strftime("Generated %Y-%m-%d %H:%M:%S"), styles['Normal']))
    story.append(Spacer(1, 6*mm))

    # Build table data
    data: List[list] = [["Image", "Filename Guess", "Model Suggestion", "Confidence"]]
    thumb_w = 50 * mm
    thumb_h = 20 * mm
    for it in items:
        try:
            img_el = RLImage(str(it.path), width=thumb_w, height=thumb_h)
        except Exception:
            img_el = Paragraph("(image error)", styles['Normal'])
        conf_txt = "" if it.confidence is None else f"{it.confidence:.3f}"
        data.append([img_el, it.wrong, it.suggested or "(none)", conf_txt])

    table = Table(data, colWidths=[thumb_w + 5*mm, 60*mm, 60*mm, 30*mm])
    table.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (3,1), (3,-1), 'RIGHT'),
    ]))
    story.append(table)
    doc.build(story)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a PDF table comparing incorrect vs suggested CAPTCHA solutions.")
    ap.add_argument("--dir", default="failures", help="Directory containing failure images (default: failures)")
    ap.add_argument("--limit", type=int, default=50, help="Max images to include (default: 50)")
    ap.add_argument("--output", default="reports/captcha_report.pdf", help="Output PDF path (default: reports/captcha_report.pdf)")
    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"no such directory: {root}", file=sys.stderr)
        return 2

    paths = iter_failure_images(root, max(1, int(args.limit)))
    if not paths:
        print("no images found")
        return 0

    solver = GiftCaptchaSolver()
    if not solver.is_initialized:
        print("ONNX solver not initialized; ensure captcha_model.onnx and metadata are present.", file=sys.stderr)
        return 3

    items: List[Item] = []
    for p in paths:
        wrong = parse_wrong_guess_from_filename(p)
        try:
            img_bytes = p.read_bytes()
            guess, ok, _method, conf, _ = asyncio.run(solver.solve_captcha(img_bytes, fid=None, attempt=0))
            items.append(Item(path=p, wrong=wrong, suggested=(guess if ok else None), confidence=(conf if ok else None)))
        except Exception as e:
            items.append(Item(path=p, wrong=wrong, suggested=None, confidence=None))

    out_path = Path(args.output)
    build_pdf(items, out_path)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

