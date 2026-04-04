"""CLI interface for CMSBOT — `cmsbot generate "Write a blog about..."`"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cmsbot.blog_draft import export_blog_draft
from cmsbot.config import Config
from cmsbot.context import build_enriched_prompt, list_styles, load_context_file, load_style
from cmsbot.pipeline import AutoreasonPipeline
from cmsbot.project import (
    combine_context_text,
    load_project_profile,
    make_project_profile,
    write_project_profile,
)
from cmsbot.screenshots import (
    copy_screenshot_into_dir,
    describe_screenshots,
    load_manifest,
    resolve_screenshot_dir,
    validate_manifest,
)
from cmsbot.topics import (
    build_cmsbot_command,
    format_topic_brief,
    latest_topic_brief_file,
    load_topic_briefs,
    merge_topic_briefs,
)
from cmsbot.voice import synthesize_voice

console = Console()


@click.group()
@click.version_option(package_name="cmsbot")
def main():
    """CMSBOT — Multi-agent content refinement using the Autoreason pattern."""
    pass


@main.command()
@click.argument("prompt")
@click.option(
    "--type", "-t",
    "content_type",
    type=click.Choice(["blog", "social"]),
    default="blog",
    help="Content type to generate.",
)
@click.option(
    "--output", "-o",
    "output_dir",
    type=click.Path(),
    default=None,
    help="Output directory (default: ./output/<slug>).",
)
@click.option(
    "--budget",
    type=float,
    default=None,
    help="Budget cap in USD (default: $75.00).",
)
@click.option(
    "--max-rounds",
    type=int,
    default=None,
    help="Maximum refinement rounds (default: 5).",
)
@click.option(
    "--context-file", "-c",
    type=click.Path(exists=True),
    default=None,
    help="Path to a context file (brand voice, audience, guidelines).",
)
@click.option(
    "--project-file", "-p",
    type=click.Path(exists=True),
    default=None,
    help="Path to a CMSBOT project profile JSON file.",
)
@click.option(
    "--style", "-s",
    default=None,
    help="Writing style preset (e.g. professional, casual, technical). Run `cmsbot styles` to list.",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True),
    default=None,
    help="Path to .env file.",
)
def generate(
    prompt: str,
    content_type: str,
    output_dir: str | None,
    budget: float | None,
    max_rounds: int | None,
    context_file: str | None,
    project_file: str | None,
    style: str | None,
    env_file: str | None,
):
    """Generate refined content using the Autoreason multi-agent pipeline."""
    # Load config
    config = Config.from_env(env_file)

    if budget is not None:
        config.budget_cap = budget
    if max_rounds is not None:
        config.max_rounds = max_rounds

    # Determine output directory
    if output_dir:
        out_path = Path(output_dir)
    else:
        slug = _slugify(prompt[:60])
        out_path = config.output_dir / slug
    config.output_dir = out_path

    # Load context and style, build enriched prompt
    context_text = None
    style_text = None
    project_context = None

    if context_file:
        try:
            context_text = load_context_file(context_file)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    if project_file:
        try:
            project = load_project_profile(project_file)
            project_context = project.render_context_markdown()
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    if style:
        try:
            style_text = load_style(style)
        except FileNotFoundError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

    task_prompt = build_enriched_prompt(
        prompt,
        context=combine_context_text(project_context, context_text),
        style=style_text,
    )

    # Verify at least one provider is configured
    available = config.available_providers()
    if not available:
        console.print("[red]Error:[/red] No API keys configured. Set at least one provider in .env")
        sys.exit(1)

    # Show startup info
    context_parts: list[str] = []
    if project_file:
        context_parts.append(Path(project_file).name)
    if context_file:
        context_parts.append(Path(context_file).name)
    context_label = " + ".join(context_parts) if context_parts else "none"
    style_label = style or "none"
    console.print()
    console.print(Panel.fit(
        f"[bold]CMSBOT Autoreason Pipeline[/bold]\n\n"
        f"[dim]Prompt:[/dim]   {prompt}\n"
        f"[dim]Type:[/dim]     {content_type}\n"
        f"[dim]Context:[/dim]  {context_label}\n"
        f"[dim]Style:[/dim]    {style_label}\n"
        f"[dim]Budget:[/dim]   ${config.budget_cap:.2f}\n"
        f"[dim]Rounds:[/dim]   max {config.max_rounds}\n"
        f"[dim]Output:[/dim]   {out_path}\n"
        f"[dim]Providers:[/dim] {', '.join(available)}",
        border_style="bright_yellow",
    ))
    console.print()

    # Run pipeline
    pipeline = AutoreasonPipeline(config)
    pipeline.set_logger(lambda msg: console.print(msg))

    try:
        result = asyncio.run(pipeline.run(
            task_prompt=task_prompt,
            content_type=content_type,
            output_dir=out_path,
        ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        sys.exit(1)

    # Show results
    _print_results(result, pipeline.budget, out_path)


@main.command("export-blog-draft")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--category", default=None, help="Optional category for the exported draft.")
@click.option("--tags", default=None, help="Comma-separated tags for the exported draft.")
@click.option("--author", default=None, help="Optional author for the exported draft.")
@click.option(
    "--status",
    type=click.Choice(["draft", "published"]),
    default="draft",
    show_default=True,
    help="Draft status to write into the exported artifact.",
)
@click.option(
    "--thumbnail-url",
    default=None,
    help="Optional thumbnail URL override. Defaults to the hero image detected in the HTML.",
)
@click.option(
    "--save",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional explicit output path. Defaults to <output_dir>/blog_draft.json.",
)
def export_blog_draft_command(
    output_dir: Path,
    category: str | None,
    tags: str | None,
    author: str | None,
    status: str,
    thumbnail_url: str | None,
    save: Path | None,
):
    """Export CMSBOT HTML output into a portable blog_draft.json artifact."""
    tag_list = [tag.strip() for tag in tags.split(",")] if tags else []

    try:
        payload, destination = export_blog_draft(
            output_dir,
            save_path=save,
            category=category,
            tags=tag_list,
            author=author,
            status=status,
            thumbnail_url=thumbnail_url,
        )
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Blog Draft Exported[/bold]\n\n"
            f"[dim]Title:[/dim] {payload['title']}\n"
            f"[dim]Slug:[/dim] {payload['slug']}\n"
            f"[dim]Status:[/dim] {payload['status']}\n"
            f"[dim]Output:[/dim] {destination}",
            border_style="green",
        )
    )


@main.command()
def check():
    """Check which API providers are configured."""
    config = Config.from_env()
    available = config.available_providers()

    table = Table(title="Provider Status")
    table.add_column("Provider", style="bold")
    table.add_column("Status")
    table.add_column("Model")

    for name in ("anthropic", "anthropic_opus", "openai", "gemini", "openai_compat"):
        cfg = getattr(config, name)
        if cfg:
            table.add_row(name, "[green]configured[/green]", cfg.model)
        else:
            table.add_row(name, "[dim]not set[/dim]", "—")

    console.print(table)


@main.command()
def styles():
    """List available writing style presets."""
    from cmsbot.context import get_local_style_dirs, get_styles_dir

    available = list_styles()
    if not available:
        console.print("[dim]No styles found.[/dim]")
        return

    table = Table(title="Available Styles")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Preview")

    builtin_dir = get_styles_dir()
    local_dirs = get_local_style_dirs()

    for name in available:
        # Determine source
        text = None
        source = "[dim]built-in[/dim]"

        for local_dir in local_dirs:
            for local_path in (
                local_dir / f"{name}.local.md",
                local_dir / f"{name}.md",
            ):
                if local_path.is_file():
                    source = f"[cyan]local[/cyan] ({local_dir})"
                    text = local_path.read_text(encoding="utf-8").strip()
                    break
            if text is not None:
                break

        if text is None:
            text = (builtin_dir / f"{name}.md").read_text(encoding="utf-8").strip()

        # First line as preview
        preview = text.split("\n")[0][:60]
        table.add_row(name, source, preview)

    console.print(table)
    console.print(f"\n[dim]Add custom styles to ./styles/*.md to override or extend.[/dim]")


@main.command("topics")
@click.option(
    "--briefs-file",
    type=click.Path(exists=True),
    default=None,
    help="Explicit topic brief JSON file. Defaults to the latest file in ./seo/output.",
)
@click.option(
    "--curated-file",
    type=click.Path(exists=True),
    default=None,
    help="Optional curated topic brief JSON file to merge in.",
)
@click.option("--limit", type=int, default=20, help="Number of topics to display.")
@click.option(
    "--command",
    "command_index",
    type=int,
    default=None,
    help="Print the cmsbot command for a specific topic index.",
)
@click.option(
    "--project-file",
    type=click.Path(exists=True),
    default=None,
    help="Project profile to include in generated commands.",
)
@click.option("--style", default=None, help="Optional writing style to include in generated commands.")
def topics(
    briefs_file: str | None,
    curated_file: str | None,
    limit: int,
    command_index: int | None,
    project_file: str | None,
    style: str | None,
):
    """List topic briefs from SEO output and optional curated sources."""
    output_dir = os.getenv("CMSBOT_SEO_OUTPUT_DIR", "seo/output")
    chosen_file = Path(briefs_file) if briefs_file else latest_topic_brief_file(output_dir)
    if chosen_file is None:
        console.print("[red]Error:[/red] No topic briefs found. Run the SEO evaluator first or pass --briefs-file.")
        sys.exit(1)

    seo_briefs = load_topic_briefs(chosen_file)
    curated_briefs = load_topic_briefs(curated_file) if curated_file else []
    merged = merge_topic_briefs(seo_briefs, curated_briefs)

    if command_index is not None:
        if command_index < 0 or command_index >= len(merged):
            console.print(f"[red]Error:[/red] Topic index {command_index} out of range (0-{max(len(merged) - 1, 0)}).")
            sys.exit(1)
        console.print(
            build_cmsbot_command(
                merged[command_index],
                project_file=project_file,
                style=style,
            )
        )
        return

    for idx, brief in enumerate(merged[:limit]):
        console.print(format_topic_brief(brief, idx))
        console.print()

    console.print(f"[dim]Loaded {len(merged)} topic briefs from {chosen_file}.[/dim]")


@main.group()
def screenshots():
    """Manage slideshow screenshot manifests."""
    pass


@screenshots.command("describe")
@click.option("--dir", "screenshot_dir", type=click.Path(path_type=Path), default=None, help="Screenshots directory.")
@click.option("--project-file", type=click.Path(exists=True), default=None, help="Project profile used to resolve screenshot_dir.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--provider", default=None, help="Vision-capable provider to describe screenshots.")
@click.option("--model", default=None, help="Optional model override for the describer.")
@click.option("--force", is_flag=True, help="Re-describe files even when checksum matches the manifest.")
def screenshots_describe(
    screenshot_dir: Path | None,
    project_file: str | None,
    env_file: str | None,
    provider: str | None,
    model: str | None,
    force: bool,
):
    """Generate or update screenshots/manifest.json using a vision model."""
    directory = resolve_screenshot_dir(screenshot_dir, project_file=project_file)
    try:
        manifest, stats = describe_screenshots(
            directory,
            env_file=env_file,
            provider=provider,
            model=model,
            force=force,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Screenshot Manifest Updated[/bold]\n\n"
            f"[dim]Directory:[/dim] {directory}\n"
            f"[dim]Entries:[/dim] {len(manifest.get('entries', {}))}\n"
            f"[dim]Described:[/dim] {stats['described']}\n"
            f"[dim]Reused:[/dim] {stats['reused']}\n"
            f"[dim]Removed:[/dim] {stats['removed']}\n"
            f"[dim]Manifest:[/dim] {directory / 'manifest.json'}",
            border_style="cyan",
        )
    )


@screenshots.command("list")
@click.option("--dir", "screenshot_dir", type=click.Path(path_type=Path), default=None, help="Screenshots directory.")
@click.option("--project-file", type=click.Path(exists=True), default=None, help="Project profile used to resolve screenshot_dir.")
def screenshots_list(screenshot_dir: Path | None, project_file: str | None):
    """List manifest refs available to slideshow generation."""
    directory = resolve_screenshot_dir(screenshot_dir, project_file=project_file)
    manifest = load_manifest(directory)
    if manifest is None or not manifest.get("entries"):
        console.print(f"[dim]No manifest entries found in {directory}.[/dim]")
        return

    table = Table(title=f"Screenshot Refs ({directory})")
    table.add_column("Ref", style="bold")
    table.add_column("File")
    table.add_column("Screen")
    table.add_column("Description")

    for key, entry in sorted(manifest.get("entries", {}).items()):
        table.add_row(
            key,
            str(entry.get("file") or ""),
            str(entry.get("screen_name") or ""),
            str(entry.get("description") or ""),
        )
    console.print(table)


@screenshots.command("validate")
@click.option("--dir", "screenshot_dir", type=click.Path(path_type=Path), default=None, help="Screenshots directory.")
@click.option("--project-file", type=click.Path(exists=True), default=None, help="Project profile used to resolve screenshot_dir.")
def screenshots_validate(screenshot_dir: Path | None, project_file: str | None):
    """Validate that manifest entries still point to real screenshot files."""
    directory = resolve_screenshot_dir(screenshot_dir, project_file=project_file)
    issues = validate_manifest(directory)
    if issues:
        console.print(f"[red]Validation failed for {directory}[/red]")
        for issue in issues:
            console.print(f"- {issue}")
        sys.exit(1)
    console.print(f"[green]Manifest valid:[/green] {directory / 'manifest.json'}")


@screenshots.command("add")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dir", "screenshot_dir", type=click.Path(path_type=Path), default=None, help="Screenshots directory.")
@click.option("--project-file", type=click.Path(exists=True), default=None, help="Project profile used to resolve screenshot_dir.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--provider", default=None, help="Vision-capable provider to describe screenshots.")
@click.option("--model", default=None, help="Optional model override for the describer.")
def screenshots_add(
    source: Path,
    screenshot_dir: Path | None,
    project_file: str | None,
    env_file: str | None,
    provider: str | None,
    model: str | None,
):
    """Copy a screenshot into the pack and update the manifest for that file."""
    directory = resolve_screenshot_dir(screenshot_dir, project_file=project_file)
    try:
        destination = copy_screenshot_into_dir(source, directory)
        manifest, _ = describe_screenshots(
            directory,
            env_file=env_file,
            provider=provider,
            model=model,
            target_files=[destination],
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    entry_key = next(
        (key for key, entry in manifest.get("entries", {}).items() if entry.get("file") == destination.name),
        None,
    )
    console.print(
        Panel.fit(
            f"[bold]Screenshot Added[/bold]\n\n"
            f"[dim]File:[/dim] {destination}\n"
            f"[dim]Ref:[/dim] {entry_key or 'unknown'}\n"
            f"[dim]Manifest:[/dim] {directory / 'manifest.json'}",
            border_style="green",
        )
    )


@main.group()
def project():
    """Project profile helpers for reusable marketing setups."""
    pass


@project.command("init")
@click.argument("name")
@click.option(
    "--output",
    type=click.Path(),
    default=None,
    help="Output JSON path (default: ./projects/<slug>.json).",
)
def project_init(name: str, output: str | None):
    """Create a starter CMSBOT project profile JSON file."""
    data = make_project_profile(name)
    destination = Path(output) if output else Path("projects") / f"{data['slug']}.json"
    path = write_project_profile(destination, data)
    console.print(f"[green]Wrote project profile:[/green] {path}")


@project.command("context")
@click.argument("project_file", type=click.Path(exists=True))
@click.option(
    "--output",
    type=click.Path(),
    default=None,
    help="Optional markdown output path. Prints to stdout when omitted.",
)
def project_context(project_file: str, output: str | None):
    """Render a markdown context file from a project profile."""
    profile = load_project_profile(project_file)
    content = profile.render_context_markdown()
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        console.print(f"[green]Wrote context file:[/green] {destination}")
        return
    console.print(content)


@project.command("voice")
@click.argument("project_file", type=click.Path(exists=True))
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--provider", default=None, help="Preferred LLM provider for voice synthesis.")
@click.option("--brief", default=None, help="Optional extra product brief to include.")
@click.option(
    "--output",
    type=click.Path(),
    default=None,
    help="Optional JSON output path. Prints to stdout when omitted.",
)
def project_voice(
    project_file: str,
    env_file: str | None,
    provider: str | None,
    brief: str | None,
    output: str | None,
):
    """Generate tone and personality flags from a project profile."""
    profile = load_project_profile(project_file)
    config = Config.from_env(env_file)

    try:
        result = asyncio.run(
            synthesize_voice(
                profile=profile,
                config=config,
                preferred_provider=provider,
                extra_brief=brief,
            )
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    rendered = json.dumps(result, indent=2)
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
        console.print(f"[green]Wrote voice profile:[/green] {destination}")
        return

    console.print(rendered)


@main.command()
def models():
    """Show available models and their pricing."""
    from cmsbot.budget import MODEL_COSTS

    table = Table(title="Model Pricing (per 1M tokens)")
    table.add_column("Model", style="bold")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")

    for model, costs in sorted(MODEL_COSTS.items()):
        table.add_row(
            model,
            f"${costs['input']:.2f}",
            f"${costs['output']:.2f}",
        )

    console.print(table)


def _print_results(result, budget, output_dir: Path):
    """Print a summary of the pipeline results."""
    console.print()

    # Budget summary
    summary = budget.summary()
    table = Table(title="Pipeline Summary", border_style="bright_yellow")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Cost", f"${summary['total_cost']:.4f}")
    table.add_row("Budget Remaining", f"${summary['remaining']:.4f}")
    table.add_row("Utilization", summary['utilization'])
    table.add_row("Input Tokens", f"{summary['total_input_tokens']:,}")
    table.add_row("Output Tokens", f"{summary['total_output_tokens']:,}")
    table.add_row("Image Cost", f"${summary['image_cost']:.4f}")
    table.add_row("API Calls", str(summary['num_api_calls']))
    table.add_row("Rounds", str(result.total_rounds))
    table.add_row("Converged", "Yes" if result.converged else "No")

    console.print(table)

    # Round details
    if result.rounds:
        rounds_table = Table(title="Round Results")
        rounds_table.add_column("Round", justify="center")
        rounds_table.add_column("Winner", justify="center")
        rounds_table.add_column("Streak", justify="center")
        rounds_table.add_column("Cost", justify="right")

        for r in result.rounds:
            winner_style = "green" if r.winner_label == "A" else "yellow" if r.winner_label == "B" else "cyan"
            rounds_table.add_row(
                str(r.round_num),
                f"[{winner_style}]{r.winner_label}[/{winner_style}]",
                str(r.streak),
                f"${r.round_cost:.4f}",
            )

        console.print(rounds_table)

    console.print(f"\n[green bold]Output saved to:[/green bold] {output_dir}/index.html")
    console.print()


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text[:60].strip('-') or "content"


if __name__ == "__main__":
    main()
