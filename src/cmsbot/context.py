"""Context and style loading — builds enriched prompts from user files."""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def get_styles_dir() -> Path:
    """Return the path to the built-in styles directory."""
    return Path(resources.files("cmsbot") / "styles")  # type: ignore[arg-type]


def list_styles() -> list[str]:
    """Return names of all available style presets (built-in + local)."""
    styles: set[str] = set()

    # Built-in styles shipped with the package
    for f in get_styles_dir().glob("*.md"):
        styles.add(f.stem)

    # User styles in local ./styles/ directory
    local = Path("styles")
    if local.is_dir():
        for f in local.glob("*.md"):
            styles.add(f.stem)

    return sorted(styles)


def load_style(name: str) -> str:
    """Load a style preset by name. Checks local ./styles/ first, then built-in."""
    # Local override takes priority
    local = Path("styles") / f"{name}.md"
    if local.is_file():
        return local.read_text(encoding="utf-8").strip()

    # Built-in
    builtin = get_styles_dir() / f"{name}.md"
    if builtin.is_file():
        return builtin.read_text(encoding="utf-8").strip()

    available = ", ".join(list_styles()) or "(none)"
    raise FileNotFoundError(
        f"Style '{name}' not found. Available styles: {available}"
    )


def load_context_file(path: str | Path) -> str:
    """Load a context file from disk."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Context file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def build_enriched_prompt(
    prompt: str,
    context: str | None = None,
    style: str | None = None,
) -> str:
    """Assemble the final task prompt from user prompt + optional context + style.

    The enriched prompt is what every agent sees as the "task".
    """
    parts: list[str] = []

    if context:
        parts.append(f"CONTEXT AND GUIDELINES:\n{context}")

    if style:
        parts.append(f"WRITING STYLE:\n{style}")

    parts.append(f"TASK:\n{prompt}")

    return "\n\n".join(parts)
