"""CLI interface for CMSBOT — `cmsbot generate "Write a blog about..."`"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cmsbot.config import Config
from cmsbot.context import build_enriched_prompt, list_styles, load_context_file, load_style
from cmsbot.pipeline import AutoreasonPipeline
from cmsbot.project import (
    combine_context_text,
    load_project_profile,
    make_project_profile,
    write_project_profile,
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
        out_path = Path("output") / slug
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
    from cmsbot.context import get_styles_dir

    available = list_styles()
    if not available:
        console.print("[dim]No styles found.[/dim]")
        return

    table = Table(title="Available Styles")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Preview")

    builtin_dir = get_styles_dir()
    local_dir = Path("styles")

    for name in available:
        # Determine source
        local_path = local_dir / f"{name}.md"
        if local_path.is_file():
            source = "[cyan]local[/cyan] (./styles/)"
            text = local_path.read_text(encoding="utf-8").strip()
        else:
            source = "[dim]built-in[/dim]"
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
    chosen_file = Path(briefs_file) if briefs_file else latest_topic_brief_file("seo/output")
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
