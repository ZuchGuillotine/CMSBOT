"""Refresh a finished multipass blog (`cmsbot refresh`): structure, tables, rule fixes and images.

For posts already written: adds ## section headings, turns existing comparisons into tables and removes
sentences the project rules forbid, without new facts (a figure check rejects any number the original
lacks). Then plans images against the new sections and re-renders index.html. The previous content and
images are kept next to the new ones. The claim ledger is unchanged because the facts are.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cmsbot.budget import BudgetTracker
from cmsbot.config import Config
from cmsbot.models import ContentType
from cmsbot.multipass import prompts
from cmsbot.multipass.derive import _numbers
from cmsbot.multipass.pipeline import find_gaps
from cmsbot.providers import build_provider
from cmsbot.renderer import Renderer

_HEADING = re.compile(r"^## \S", re.M)
_TABLE_SEPARATOR = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+\s*$", re.M)
HERO_NAMES = ("hero.jpg", "hero.png", "hero.webp")


class RefreshError(RuntimeError):
    pass


def new_figures(original: str, revised: str, allowed_text: str = "") -> list[str]:
    """Numbers in the revision that the original (or allowed text) doesn't contain."""
    return sorted(_numbers(revised) - _numbers(original + "\n" + allowed_text), key=lambda n: (len(n), n))


def structure_counts(markdown: str) -> dict[str, int]:
    return {"headings": len(_HEADING.findall(markdown)), "tables": len(_TABLE_SEPARATOR.findall(markdown))}


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def existing_images(source: Path, title: str) -> list[Any]:
    """ImageSpecs for images already on disk (hero.* and section-N.*), for a re-render without new images."""
    from cmsbot.models import ImageSpec

    specs = []
    for f in sorted((source / "images").glob("*")) if (source / "images").exists() else []:
        placement = f.stem if f.stem == "hero" or re.fullmatch(r"section-\d+", f.stem) else None
        if placement:
            specs.append(ImageSpec(description=placement, alt_text=title if placement == "hero" else "",
                                   placement=placement, method="existing", file_path=f"images/{f.name}"))
    return specs


class RefreshPipeline:
    def __init__(self, config: Config | None = None, *, images: bool = True):
        self.config = config or Config.from_env()
        self.budget = BudgetTracker(cap=self.config.budget_cap)
        self.images = images
        self._log: Callable[[str], None] = lambda msg: None

    def set_logger(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    async def _call(self, stage: str, system: str, user: str) -> str:
        provider = build_provider(self.config.provider_for("restructurer"), self.config)
        response = await provider.generate_simple(user_prompt=user, system_prompt=system, max_tokens=8000)
        self.budget.record(stage, response.provider, response.model, response.input_tokens, response.output_tokens)
        self._log(f"  [{stage}] {response.model} | {response.input_tokens}+{response.output_tokens} tokens | ${response.cost:.4f}")
        return _strip_fences(response.content)

    async def restructure(self, brief: str, material: str, original: str) -> str:
        revised = await self._call(
            "refresh", prompts.REFRESH_SYSTEM, f"BRIEF:\n{brief}\n\nMATERIAL:\n{material or 'None.'}\n\nARTICLE:\n{original}"
        )
        extra = new_figures(original, revised)
        if extra:
            self._log(f"  [refresh] new figures {extra}; asking for a fix")
            revised = await self._call(
                "refresh_fix", prompts.REFRESH_FIX_SYSTEM,
                f"ORIGINAL:\n{original}\n\nREVISION:\n{revised}\n\nFIGURES NOT IN THE ORIGINAL: {', '.join(extra)}",
            )
            extra = new_figures(original, revised)
            if extra:
                raise RefreshError(f"revision still adds figures not in the original: {', '.join(extra)}")
        if not revised.startswith("# "):
            raise RefreshError("revision lost the '# ' title line")
        return revised

    async def run(self, asset_root: Path, brief: str, task_prompt: str) -> dict[str, Any]:
        source = asset_root / "source"
        content_path = source / "content.md"
        if not content_path.exists():
            raise RefreshError(f"no content at {content_path}")
        original = content_path.read_text(encoding="utf-8").strip()
        material_path = source / "material.json"
        material = material_path.read_text(encoding="utf-8") if material_path.exists() else ""

        self._log("[1/3 Structure] headings, tables, rule fixes...")
        revised = await self.restructure(brief, material, original)
        counts = structure_counts(revised)
        self._log(f"  {counts['headings']} headings, {counts['tables']} tables")

        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = source / f"pre-refresh-{stamp}"
        backup.mkdir(parents=True)
        for name in ("content.md", "index.html", "gaps.json"):
            if (source / name).exists():
                shutil.copy2(source / name, backup / name)

        images: list[Any] = []
        if self.images:
            from cmsbot.pipeline import build_image_director

            self._log("[2/3 Images] planning against the new sections...")
            images_dir = source / "images"
            old_hero = next((images_dir / n for n in HERO_NAMES if (images_dir / n).exists()), None)
            if images_dir.exists():
                shutil.move(str(images_dir), str(backup / "images"))
            # Keep the hero readers (and the published thumbnail) already know; plan sections only
            images, responses = await build_image_director(self.config, self.budget).generate_images(
                task_prompt, revised, ContentType.BLOG, source, log_fn=self._log, skip_hero=old_hero is not None
            )
            for r in responses:
                self.budget.record("image_director", r.provider, r.model, r.input_tokens, r.output_tokens)
            if old_hero is not None:
                from cmsbot.models import ImageSpec

                (source / "images").mkdir(exist_ok=True)
                shutil.copy2(backup / "images" / old_hero.name, source / "images" / old_hero.name)
                images.insert(0, ImageSpec(description="hero", alt_text=revised.splitlines()[0][2:].strip(),
                                           placement="hero", method="existing", file_path=f"images/{old_hero.name}"))
        else:
            self._log("[2/3 Images] skipped (--no-images); keeping existing images")
            images = existing_images(source, revised.splitlines()[0][2:].strip())

        self._log("[3/3 Render]")
        html = Renderer().render(content=revised, images=images, content_type=ContentType.BLOG, task_prompt=task_prompt)
        content_path.write_text(revised + "\n", encoding="utf-8")
        (source / "index.html").write_text(html, encoding="utf-8")
        gaps = find_gaps(revised)
        (source / "gaps.json").write_text(json.dumps({"gaps": gaps}, indent=2) + "\n", encoding="utf-8")
        report = {
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "backup": backup.name,
            **counts,
            "images": [{"placement": s.placement, "file": s.file_path} for s in images if s.file_path],
            "gaps": len(gaps),
            "cost": round(self.budget.spent, 4),
        }
        (source / "refresh.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
