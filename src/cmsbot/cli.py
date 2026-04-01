"""CLI interface for CMSBOT — `cmsbot generate "Write a blog about..."`"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cmsbot.config import Config
from cmsbot.pipeline import AutoreasonPipeline

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

    # Verify at least one provider is configured
    available = config.available_providers()
    if not available:
        console.print("[red]Error:[/red] No API keys configured. Set at least one provider in .env")
        sys.exit(1)

    # Show startup info
    console.print()
    console.print(Panel.fit(
        f"[bold]CMSBOT Autoreason Pipeline[/bold]\n\n"
        f"[dim]Prompt:[/dim]  {prompt}\n"
        f"[dim]Type:[/dim]    {content_type}\n"
        f"[dim]Budget:[/dim]  ${config.budget_cap:.2f}\n"
        f"[dim]Rounds:[/dim]  max {config.max_rounds}\n"
        f"[dim]Output:[/dim]  {out_path}\n"
        f"[dim]Providers:[/dim] {', '.join(available)}",
        border_style="bright_yellow",
    ))
    console.print()

    # Run pipeline
    pipeline = AutoreasonPipeline(config)
    pipeline.set_logger(lambda msg: console.print(msg))

    try:
        result = asyncio.run(pipeline.run(
            task_prompt=prompt,
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

    for name in ("anthropic", "openai", "gemini", "openai_compat"):
        cfg = getattr(config, name)
        if cfg:
            table.add_row(name, "[green]configured[/green]", cfg.model)
        else:
            table.add_row(name, "[dim]not set[/dim]", "—")

    console.print(table)


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
