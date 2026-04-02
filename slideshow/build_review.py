#!/usr/bin/env python3
"""
build_review.py — Generate an HTML review page for a batch of slideshows.

Opens locally in a browser for quick approve/reject/edit workflow.

Usage:
  python build_review.py --batch output/batch_2026-04-01/
"""

import argparse
import json
import base64
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def image_to_data_uri(path: Path) -> str:
    """Convert an image file to a base64 data URI for embedding in HTML."""
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{data}"


def build_review_html(batch_dir: Path) -> Path:
    """Generate the review HTML file for a batch."""
    batch_path = Path(batch_dir)
    slideshow_dirs = sorted([
        d for d in batch_path.iterdir()
        if d.is_dir() and (d / "script.txt").exists()
    ])

    if not slideshow_dirs:
        print(f"No slideshows found in {batch_path}")
        return None

    # Collect all slideshow data
    slideshows = []
    for show_dir in slideshow_dirs:
        show_data = {
            "id": show_dir.name,
            "dir": str(show_dir),
            "slides": [],
            "script": "",
            "caption": "",
            "metadata": {},
        }

        # Load script
        script_path = show_dir / "script.txt"
        if script_path.exists():
            show_data["script"] = script_path.read_text()

        # Load caption
        caption_path = show_dir / "caption.txt"
        if caption_path.exists():
            show_data["caption"] = caption_path.read_text()

        # Load metadata
        meta_path = show_dir / "metadata.json"
        if meta_path.exists():
            with open(meta_path) as f:
                show_data["metadata"] = json.load(f)

        # Collect images (prefer processed, fallback to originals)
        processed_dir = show_dir / "processed"
        images_dir = show_dir / "images"
        img_source = processed_dir if processed_dir.exists() else images_dir

        if img_source.exists():
            for img_path in sorted(img_source.glob("*")):
                if img_path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    show_data["slides"].append({
                        "filename": img_path.name,
                        "data_uri": image_to_data_uri(img_path),
                    })

        slideshows.append(show_data)

    # Generate HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Slideshow Review — {batch_path.name}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #111; color: #eee; padding: 20px; }}
  h1 {{ margin-bottom: 10px; font-size: 24px; }}
  .batch-info {{ color: #888; margin-bottom: 30px; font-size: 14px; }}
  .slideshow {{ background: #1a1a1a; border-radius: 12px; padding: 24px; margin-bottom: 30px; border: 2px solid #333; }}
  .slideshow.approved {{ border-color: #22c55e; }}
  .slideshow.rejected {{ border-color: #ef4444; opacity: 0.5; }}
  .slideshow-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
  .slideshow-title {{ font-size: 18px; font-weight: 600; }}
  .slideshow-format {{ color: #888; font-size: 13px; background: #333; padding: 4px 10px; border-radius: 6px; }}
  .slides-row {{ display: flex; gap: 12px; overflow-x: auto; padding-bottom: 12px; }}
  .slide {{ flex-shrink: 0; width: 180px; }}
  .slide img {{ width: 180px; height: 320px; object-fit: cover; border-radius: 8px; background: #222; }}
  .slide-placeholder {{ width: 180px; height: 320px; border-radius: 8px; background: #222; display: flex; align-items: center; justify-content: center; color: #666; font-size: 12px; }}
  .slide-num {{ font-size: 11px; color: #888; margin-top: 4px; text-align: center; }}
  .script-section {{ margin-top: 16px; }}
  .script-section h3 {{ font-size: 14px; color: #888; margin-bottom: 8px; }}
  .script-text {{ font-size: 13px; line-height: 1.6; white-space: pre-wrap; background: #222; padding: 12px; border-radius: 8px; max-height: 200px; overflow-y: auto; }}
  .caption-text {{ font-size: 13px; color: #aaa; margin-top: 8px; font-style: italic; }}
  .warnings {{ margin-top: 12px; }}
  .warning {{ background: #3d2200; color: #fbbf24; padding: 6px 10px; border-radius: 6px; font-size: 12px; margin-bottom: 4px; }}
  .actions {{ display: flex; gap: 10px; margin-top: 16px; }}
  .btn {{ padding: 8px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: all 0.2s; }}
  .btn-approve {{ background: #22c55e; color: #000; }}
  .btn-approve:hover {{ background: #16a34a; }}
  .btn-reject {{ background: #ef4444; color: #fff; }}
  .btn-reject:hover {{ background: #dc2626; }}
  .btn-edit {{ background: #3b82f6; color: #fff; }}
  .btn-edit:hover {{ background: #2563eb; }}
  .stats {{ position: fixed; top: 20px; right: 20px; background: #222; padding: 16px; border-radius: 10px; z-index: 100; font-size: 13px; }}
  .stats div {{ margin-bottom: 4px; }}
  .stat-approved {{ color: #22c55e; }}
  .stat-rejected {{ color: #ef4444; }}
  .stat-pending {{ color: #888; }}
  .export-btn {{ margin-top: 12px; padding: 8px 16px; background: #8b5cf6; color: #fff; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; width: 100%; }}
  .export-btn:hover {{ background: #7c3aed; }}
</style>
</head>
<body>

<h1>Slideshow Review</h1>
<div class="batch-info">Batch: {batch_path.name} | {len(slideshows)} slideshows | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>

<div class="stats" id="stats">
  <div class="stat-approved">Approved: <span id="count-approved">0</span></div>
  <div class="stat-rejected">Rejected: <span id="count-rejected">0</span></div>
  <div class="stat-pending">Pending: <span id="count-pending">{len(slideshows)}</span></div>
  <button class="export-btn" onclick="exportDecisions()">Export Decisions</button>
</div>
"""

    for show in slideshows:
        meta = show["metadata"]
        format_name = meta.get("format", "unknown")
        warnings = meta.get("warnings", [])
        warnings_html = "".join(f'<div class="warning">⚠ {w}</div>' for w in warnings)

        slides_html = ""
        for slide in show["slides"]:
            if slide["data_uri"]:
                slides_html += f'''
                <div class="slide">
                  <img src="{slide['data_uri']}" alt="{slide['filename']}">
                  <div class="slide-num">{slide['filename']}</div>
                </div>'''
            else:
                slides_html += f'''
                <div class="slide">
                  <div class="slide-placeholder">No image yet</div>
                  <div class="slide-num">{slide['filename']}</div>
                </div>'''

        html += f"""
<div class="slideshow" id="show-{show['id']}" data-status="pending">
  <div class="slideshow-header">
    <div class="slideshow-title">{show['id']}</div>
    <div class="slideshow-format">{format_name}</div>
  </div>

  <div class="slides-row">{slides_html}</div>

  <div class="script-section">
    <h3>Overlay Script (type natively in TikTok)</h3>
    <div class="script-text">{show['script']}</div>
  </div>

  <div class="caption-text">{show['caption']}</div>

  <div class="warnings">{warnings_html}</div>

  <div class="actions">
    <button class="btn btn-approve" onclick="decide('{show['id']}', 'approved')">Approve</button>
    <button class="btn btn-reject" onclick="decide('{show['id']}', 'rejected')">Reject</button>
    <button class="btn btn-edit" onclick="editShow('{show['id']}')">Edit Script</button>
  </div>
</div>
"""

    html += """
<script>
const decisions = {};

function decide(id, status) {
  decisions[id] = status;
  const el = document.getElementById('show-' + id);
  el.className = 'slideshow ' + status;
  el.dataset.status = status;
  updateStats();
}

function editShow(id) {
  const el = document.getElementById('show-' + id);
  const scriptEl = el.querySelector('.script-text');
  if (scriptEl.contentEditable === 'true') {
    scriptEl.contentEditable = 'false';
    scriptEl.style.border = 'none';
  } else {
    scriptEl.contentEditable = 'true';
    scriptEl.style.border = '2px solid #3b82f6';
    scriptEl.focus();
  }
}

function updateStats() {
  const shows = document.querySelectorAll('.slideshow');
  let approved = 0, rejected = 0, pending = 0;
  shows.forEach(s => {
    const status = s.dataset.status;
    if (status === 'approved') approved++;
    else if (status === 'rejected') rejected++;
    else pending++;
  });
  document.getElementById('count-approved').textContent = approved;
  document.getElementById('count-rejected').textContent = rejected;
  document.getElementById('count-pending').textContent = pending;
}

function exportDecisions() {
  // Collect edited scripts too
  const shows = document.querySelectorAll('.slideshow');
  const export_data = [];
  shows.forEach(s => {
    const id = s.id.replace('show-', '');
    const script = s.querySelector('.script-text').textContent;
    export_data.push({
      id: id,
      status: decisions[id] || 'pending',
      edited_script: script
    });
  });

  const blob = new Blob([JSON.stringify(export_data, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'review_decisions.json';
  a.click();
}
</script>
</body>
</html>
"""

    output_path = batch_path / "review.html"
    output_path.write_text(html)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate review HTML for slideshow batches")
    parser.add_argument("--batch", required=True, help="Batch directory to review")
    args = parser.parse_args()

    output = build_review_html(Path(args.batch))
    if output:
        print(f"Review page generated: {output}")
        print(f"Open in browser: file://{output.resolve()}")


if __name__ == "__main__":
    main()
