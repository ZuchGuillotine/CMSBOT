"""CLI interface for CMSBOT — `cmsbot generate "Write a blog about..."`"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cmsbot.analytics import (
    import_seo_evaluation as import_seo_evaluation_report,
    import_social_csv,
    load_performance_index,
    record_social_metrics,
    render_performance_report,
)
from cmsbot.blog_draft import export_blog_draft
from cmsbot.config import Config
from cmsbot.content_ops import (
    build_content_calendar,
    initialize_generate_layout,
    load_content_calendar,
    prepare_generate_layout,
    write_asset_manifest,
    write_content_calendar,
    write_source_brief,
)
from cmsbot.context import build_enriched_prompt, list_styles, load_context_file, load_style
from cmsbot.de_ai import review_de_ai
from cmsbot.gates import evaluate_asset_gates, record_human_approval
from cmsbot.goals import (
    initialize_ops_scaffold,
    load_goal_statuses,
    prepare_goal_handoff,
    summarize_goal_run,
)
from cmsbot.multipass import MultipassPipeline
from cmsbot.pipeline import AutoreasonPipeline
from cmsbot.project import (
    combine_context_text,
    load_project_profile,
    make_project_profile,
    write_project_profile,
)
from cmsbot.routing import decide_model_route, load_model_routing
from cmsbot.social_derivatives import export_carousel, export_video_script
from cmsbot.social_threads import export_social_thread
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
from cmsbot.workflow import finish_run, load_latest_runs, start_run

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
    "--asset-set-id",
    default=None,
    help="Canonical asset-set id for default output layout, e.g. a calendar entry id.",
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
@click.option(
    "--execution-mode",
    type=click.Choice(["autoreason", "single_model", "agent_assisted", "human_seeded"]),
    default="autoreason",
    show_default=True,
    help="Execution strategy marker. Only autoreason runs in-core; other modes are for external agent workflows.",
)
@click.option(
    "--method",
    type=click.Choice(["autoreason", "multipass"]),
    default="autoreason",
    show_default=True,
    help="Long-form method. multipass plans structure first, grounds in PubMed, audits shape, and verifies citations.",
)
@click.option("--no-pubmed", is_flag=True, default=False, help="multipass: skip the PubMed source search.")
@click.option("--no-images", is_flag=True, default=False, help="multipass: skip image generation.")
def generate(
    prompt: str,
    content_type: str,
    output_dir: str | None,
    asset_set_id: str | None,
    budget: float | None,
    max_rounds: int | None,
    context_file: str | None,
    project_file: str | None,
    style: str | None,
    env_file: str | None,
    execution_mode: str,
    method: str,
    no_pubmed: bool,
    no_images: bool,
):
    """Generate refined content using the Autoreason or multipass pipeline."""
    if execution_mode != "autoreason":
        console.print(
            "[red]Error:[/red] In-core generation currently supports only `autoreason`. "
            "Use `cmsbot goal prepare` for agent_assisted or human_seeded workflows."
        )
        sys.exit(1)

    # Load config
    config = Config.from_env(env_file)
    project = None

    if budget is not None:
        config.budget_cap = budget
    if max_rounds is not None:
        config.max_rounds = max_rounds

    output_root = config.output_dir
    if method == "multipass" and content_type != "blog":
        console.print("[red]Error:[/red] --method multipass supports --type blog only.")
        sys.exit(1)

    layout = prepare_generate_layout(
        config.output_dir,
        prompt,
        requested_output_dir=Path(output_dir) if output_dir else None,
        requested_asset_set_id=asset_set_id,
    )
    initialize_generate_layout(layout)
    config.output_dir = layout.source_dir

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
    write_source_brief(
        layout,
        prompt=prompt,
        task_prompt=task_prompt,
        content_type=content_type,
        project_name=project.name if project else None,
        project_slug=project.slug if project else None,
        project_file=project_file,
        context_file=context_file,
        style=style,
        execution_mode=execution_mode,
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
        f"[bold]CMSBOT {'Multipass' if method == 'multipass' else 'Autoreason'} Pipeline[/bold]\n\n"
        f"[dim]Prompt:[/dim]   {prompt}\n"
        f"[dim]Type:[/dim]     {content_type}\n"
        f"[dim]Context:[/dim]  {context_label}\n"
        f"[dim]Style:[/dim]    {style_label}\n"
        f"[dim]Budget:[/dim]   ${config.budget_cap:.2f}\n"
        f"[dim]Rounds:[/dim]   max {config.max_rounds}\n"
        f"[dim]Asset Set:[/dim] {layout.asset_set_dir}\n"
        f"[dim]Source:[/dim]   {layout.source_dir}\n"
        f"[dim]Providers:[/dim] {', '.join(available)}",
        border_style="bright_yellow",
    ))
    console.print()

    # Run pipeline
    artifacts = None
    if method == "multipass":
        pipeline = MultipassPipeline(config, use_pubmed=not no_pubmed, images=not no_images)
    else:
        pipeline = AutoreasonPipeline(config)
    pipeline.set_logger(lambda msg: console.print(msg))

    try:
        if method == "multipass":
            result, artifacts = asyncio.run(pipeline.run(
                task_prompt, layout.source_dir, ledger_dir=output_root,
            ))
        else:
            result = asyncio.run(pipeline.run(
                task_prompt=task_prompt,
                content_type=content_type,
                output_dir=layout.source_dir,
            ))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        sys.exit(1)

    manifest_path = write_asset_manifest(
        layout,
        prompt=prompt,
        task_prompt=task_prompt,
        content_type=content_type,
        project_name=project.name if project else None,
        project_slug=project.slug if project else None,
        project_file=project_file,
        context_file=context_file,
        style=style,
        pipeline_result=result,
        budget_summary=pipeline.budget.summary(),
        execution_mode=execution_mode,
    )

    # Show results
    _print_results(
        result,
        pipeline.budget,
        asset_set_dir=layout.asset_set_dir,
        source_dir=layout.source_dir,
        manifest_path=manifest_path,
    )
    if artifacts is not None:
        unverified = [c for c in artifacts.claims if c["verification"]["status"] != "verified"]
        console.print(f"\n[bold]Review notes[/bold]: {len(artifacts.gaps)} gap(s) to fill, "
                      f"{len(unverified)} claim(s) without a verified citation.")
        for gap in artifacts.gaps:
            console.print(f"  [yellow][{gap}][/yellow]")


@main.command()
@click.argument("asset_set_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--kind", "kinds", multiple=True, required=True,
              type=click.Choice(["newsletter", "thread", "video_script"]),
              help="Derivative kind; repeat for several (e.g. --kind newsletter --kind thread).")
@click.option("--count", type=int, default=None,
              help="Items per kind (defaults: newsletter 1, thread 5, video_script 3).")
@click.option("--focus", default=None, help="newsletter: this week's focus area (e.g. 'sleep and recovery').")
@click.option("--claims-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Claim ledger JSON ({\"claims\": [...]}); default: the anchor's source/claims.json.")
@click.option("--include-unverified", is_flag=True, default=False,
              help="Also use cited claims whose citation wasn't verified.")
@click.option("--anchor-url", default=None,
              help="Public URL of the anchor article; newsletters keep {{anchor_url}} when omitted.")
@click.option("--context-file", "-c", type=click.Path(exists=True), default=None)
@click.option("--project-file", "-p", type=click.Path(exists=True), default=None)
@click.option("--style", "-s", default=None)
@click.option("--env-file", type=click.Path(exists=True), default=None)
@click.option("--budget", type=float, default=None, help="Budget cap in USD per kind.")
def derive(
    asset_set_dir: Path,
    kinds: tuple[str, ...],
    count: int | None,
    focus: str | None,
    claims_file: Path | None,
    include_unverified: bool,
    anchor_url: str | None,
    context_file: str | None,
    project_file: str | None,
    style: str | None,
    env_file: str | None,
    budget: float | None,
):
    """Write newsletter, thread and video-script derivatives of an anchor piece (multipass light run).

    Derivatives may only restate claims from the anchor's claim ledger. Output goes to
    <asset-set>/distribution/derivatives/<kind>.json.
    """
    from cmsbot.content_ops import resolve_asset_set_root
    from cmsbot.multipass.derive import ANCHOR_URL_TOKEN, DeriveError, DerivePipeline, load_ledger, write_derivatives

    config = Config.from_env(env_file)
    if budget is not None:
        config.budget_cap = budget
    asset_root = resolve_asset_set_root(asset_set_dir)
    source_dir = asset_root / "source"
    content_path = source_dir / "content.md"
    if not content_path.exists():
        console.print(f"[red]Error:[/red] No anchor content at {content_path}")
        sys.exit(1)
    anchor_text = content_path.read_text(encoding="utf-8").strip()
    title_line = next((line for line in anchor_text.splitlines() if line.startswith("# ")), "# Untitled")
    claims_path = claims_file or source_dir / "claims.json"
    if not claims_path.exists():
        console.print(f"[red]Error:[/red] No claim ledger at {claims_path}. Run generate --method multipass first, or pass --claims-file.")
        sys.exit(1)
    claims = json.loads(claims_path.read_text(encoding="utf-8")).get("claims", [])
    ledger = load_ledger(claims, include_unverified=include_unverified)

    project_context = load_project_profile(project_file).render_context_markdown() if project_file else None
    brief = build_enriched_prompt(
        "Write derivatives of the anchor article below for the project's channels.",
        context=combine_context_text(project_context, load_context_file(context_file) if context_file else None),
        style=load_style(style) if style else None,
    )

    console.print(f"[bold]Derive[/bold] {', '.join(kinds)} from {asset_root.name} ({len(ledger)} of {len(claims)} claims usable)")
    for kind in kinds:
        pipeline = DerivePipeline(config)
        pipeline.set_logger(lambda msg: console.print(msg))
        try:
            payload = asyncio.run(pipeline.run(
                kind, brief=brief, anchor_title=title_line[2:].strip(), anchor_text=anchor_text,
                ledger=ledger, count=count, focus=focus, anchor_url=anchor_url or ANCHOR_URL_TOKEN,
            ))
        except DeriveError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)
        path = write_derivatives(asset_root, payload)
        console.print(f"[green]Wrote[/green] {path}")
        for i, item in enumerate(payload["items"], 1):
            notes = item["audit"]["remaining"] + [f"[{g}]" for g in item["gaps"]]
            if notes:
                console.print(f"  item {i}: " + "; ".join(notes[:5]))


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


@main.command("export-thread")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--project-file", type=click.Path(exists=True), required=True, help="CMSBOT project profile JSON.")
@click.option("--context-file", type=click.Path(exists=True), default=None, help="Optional extra brand/context document.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--section", type=int, default=None, help="1-based section index to repurpose.")
@click.option("--section-title", default=None, help="Exact section heading to repurpose.")
@click.option("--provider", default=None, help="Optional provider override for thread generation.")
@click.option(
    "--image-mode",
    type=click.Choice(["auto", "reuse", "generate", "none"]),
    default="auto",
    show_default=True,
    help="Reuse existing blog images when possible, or generate a social visual with the existing image pipeline.",
)
@click.option(
    "--save-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional explicit output directory. Defaults to <output_dir>/threads.",
)
def export_thread_command(
    output_dir: Path,
    project_file: str,
    context_file: str | None,
    env_file: str | None,
    section: int | None,
    section_title: str | None,
    provider: str | None,
    image_mode: str,
    save_dir: Path | None,
):
    """Export a Threads/X-ready thread from a generated CMSBOT blog post."""
    try:
        payload, json_path, md_path = export_social_thread(
            output_dir,
            project_file=project_file,
            context_file=context_file,
            env_file=env_file,
            section=section,
            section_title=section_title,
            provider_name=provider,
            image_mode=image_mode,
            save_dir=save_dir,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    media_count = sum(len(post.get("media_paths") or []) for post in payload["posts"])
    console.print(
        Panel.fit(
            f"[bold]Thread Exported[/bold]\n\n"
            f"[dim]Project:[/dim] {payload['project']}\n"
            f"[dim]Article:[/dim] {payload['source']['article_title']}\n"
            f"[dim]Section:[/dim] {payload['source']['section_title']}\n"
            f"[dim]Posts:[/dim] {len(payload['posts'])}\n"
            f"[dim]Media:[/dim] {media_count}\n"
            f"[dim]JSON:[/dim] {json_path}\n"
            f"[dim]Markdown:[/dim] {md_path}",
            border_style="magenta",
        )
    )


@main.command("export-carousel")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--project-file", type=click.Path(exists=True), required=True, help="CMSBOT project profile JSON.")
@click.option("--context-file", type=click.Path(exists=True), default=None, help="Optional extra brand/context document.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--section", type=int, default=None, help="1-based section index to repurpose.")
@click.option("--section-title", default=None, help="Exact section heading to repurpose.")
@click.option("--provider", default=None, help="Optional provider override for carousel generation.")
@click.option(
    "--platform",
    type=click.Choice(["instagram", "linkedin"]),
    default="instagram",
    show_default=True,
    help="Target platform for the carousel package.",
)
@click.option(
    "--save-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional explicit output directory. Defaults to distribution/<platform> for asset sets.",
)
def export_carousel_command(
    output_dir: Path,
    project_file: str,
    context_file: str | None,
    env_file: str | None,
    section: int | None,
    section_title: str | None,
    provider: str | None,
    platform: str,
    save_dir: Path | None,
):
    """Export a creator-ready carousel package from a generated CMSBOT blog post."""
    try:
        payload, json_path, md_path, caption_path = export_carousel(
            output_dir,
            project_file=project_file,
            context_file=context_file,
            env_file=env_file,
            section=section,
            section_title=section_title,
            provider_name=provider,
            platform=platform,
            save_dir=save_dir,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Carousel Exported[/bold]\n\n"
            f"[dim]Project:[/dim] {payload['project']}\n"
            f"[dim]Platform:[/dim] {payload['platform']}\n"
            f"[dim]Section:[/dim] {payload['source']['section_title']}\n"
            f"[dim]Slides:[/dim] {len(payload['slides'])}\n"
            f"[dim]JSON:[/dim] {json_path}\n"
            f"[dim]Markdown:[/dim] {md_path}\n"
            f"[dim]Caption:[/dim] {caption_path}",
            border_style="cyan",
        )
    )


@main.command("export-video-script")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--project-file", type=click.Path(exists=True), required=True, help="CMSBOT project profile JSON.")
@click.option("--context-file", type=click.Path(exists=True), default=None, help="Optional extra brand/context document.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--section", type=int, default=None, help="1-based section index to repurpose.")
@click.option("--section-title", default=None, help="Exact section heading to repurpose.")
@click.option("--provider", default=None, help="Optional provider override for video-script generation.")
@click.option(
    "--platform",
    type=click.Choice(["tiktok", "instagram", "youtube_shorts"]),
    default="tiktok",
    show_default=True,
    help="Target platform for the short-form video package.",
)
@click.option(
    "--save-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional explicit output directory. Defaults to distribution/<platform> for asset sets.",
)
def export_video_script_command(
    output_dir: Path,
    project_file: str,
    context_file: str | None,
    env_file: str | None,
    section: int | None,
    section_title: str | None,
    provider: str | None,
    platform: str,
    save_dir: Path | None,
):
    """Export a creator-ready short-form video script package from generated CMSBOT content."""
    try:
        payload, json_path, script_path, storyboard_path = export_video_script(
            output_dir,
            project_file=project_file,
            context_file=context_file,
            env_file=env_file,
            section=section,
            section_title=section_title,
            provider_name=provider,
            platform=platform,
            save_dir=save_dir,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Video Script Exported[/bold]\n\n"
            f"[dim]Project:[/dim] {payload['project']}\n"
            f"[dim]Platform:[/dim] {payload['platform']}\n"
            f"[dim]Section:[/dim] {payload['source']['section_title']}\n"
            f"[dim]Beats:[/dim] {len(payload['beats'])}\n"
            f"[dim]JSON:[/dim] {json_path}\n"
            f"[dim]Script:[/dim] {script_path}\n"
            f"[dim]Storyboard:[/dim] {storyboard_path}",
            border_style="bright_magenta",
        )
    )


@main.command()
def check():
    """Check which API providers are configured."""
    config = Config.from_env()

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
    console.print("\n[dim]Add custom styles to ./styles/*.md to override or extend.[/dim]")


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
def goal():
    """Manage repo-backed goals and resumable workflow state."""
    pass


@goal.command("init")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repository or project root where goals/ and strategy/ should be created.",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing goal and strategy files.")
def goal_init(root: Path, overwrite: bool):
    """Create default goal specs and strategy memory files."""
    written = initialize_ops_scaffold(root, overwrite=overwrite)
    if not written:
        console.print(f"[dim]No files written. Existing scaffold preserved under {root}.[/dim]")
        return

    table = Table(title="CMSBOT Goal Scaffold")
    table.add_column("File", style="bold")
    for path in written:
        table.add_row(str(path))
    console.print(table)


@goal.command("status")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repository or project root containing goals/.",
)
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--runs", type=int, default=5, show_default=True, help="Number of recent workflow runs to show.")
def goal_status(root: Path, env_file: str | None, runs: int):
    """Show durable goal status and recent run records."""
    statuses = load_goal_statuses(root)
    if statuses:
        table = Table(title=f"Goals ({root / 'goals'})")
        table.add_column("Goal", style="bold")
        table.add_column("Status")
        table.add_column("Cadence")
        table.add_column("Owner")
        table.add_column("Budget")
        table.add_column("File")
        for entry in statuses:
            table.add_row(
                entry.title,
                entry.status,
                entry.cadence,
                entry.owner,
                entry.budget,
                str(entry.path),
            )
        console.print(table)
    else:
        console.print(f"[dim]No goal specs found under {root / 'goals'}. Run `cmsbot goal init`.[/dim]")

    config = Config.from_env(env_file)
    recent_runs = load_latest_runs(config.output_dir, limit=runs)
    if not recent_runs:
        console.print(f"[dim]No workflow runs found under {config.output_dir / 'runs'}.[/dim]")
        return

    run_table = Table(title=f"Recent Runs ({config.output_dir / 'runs'})")
    run_table.add_column("Run ID", style="bold")
    run_table.add_column("Kind")
    run_table.add_column("Status")
    run_table.add_column("Finished")
    run_table.add_column("Next Safe Action")
    for record in recent_runs:
        run_table.add_row(
            str(record.get("run_id") or ""),
            str(record.get("kind") or ""),
            str(record.get("status") or ""),
            str(record.get("finished_at") or ""),
            str(record.get("next_safe_action") or "")[:80],
        )
    console.print(run_table)


@goal.command("prepare")
@click.argument("goal_name")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repository or project root containing goals/ and strategy/.",
)
@click.option("--project-file", type=click.Path(exists=True), default=None, help="Optional project profile to include in the handoff.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def goal_prepare(goal_name: str, root: Path, project_file: str | None, env_file: str | None):
    """Prepare a resumable handoff bundle for a scheduled goal run."""
    config = Config.from_env(env_file)
    try:
        handoff, run_path, handoff_path = prepare_goal_handoff(
            root,
            config.output_dir,
            goal=goal_name,
            project_file=project_file,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Goal Handoff Prepared[/bold]\n\n"
            f"[dim]Goal:[/dim]    {handoff['goal']}\n"
            f"[dim]Run:[/dim]     {handoff['run_id']}\n"
            f"[dim]Record:[/dim]  {run_path}\n"
            f"[dim]Handoff:[/dim] {handoff_path}",
            border_style="cyan",
        )
    )


@goal.command("summarize")
@click.option("--run-id", default=None, help="Run id to summarize. Defaults to the latest run.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def goal_summarize(run_id: str | None, env_file: str | None):
    """Write a markdown summary for a workflow run."""
    config = Config.from_env(env_file)
    try:
        record, summary_path = summarize_goal_run(config.output_dir, run_id=run_id)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    console.print(
        Panel.fit(
            f"[bold]Run Summary Written[/bold]\n\n"
            f"[dim]Run:[/dim]     {record.get('run_id')}\n"
            f"[dim]Status:[/dim]  {record.get('status')}\n"
            f"[dim]Summary:[/dim] {summary_path}",
            border_style="cyan",
        )
    )


@main.group()
def review():
    """Review generated artifacts before approval or publishing."""
    pass


@review.command("de-ai")
@click.argument("asset_set_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--platform", required=True, help="Platform name such as blog, x, instagram, linkedin, or tiktok.")
@click.option("--format", "content_format", required=True, help="Artifact format such as blog, thread, carousel, or video_script.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--provider", default=None, help="Optional provider for LLM-assisted review.")
@click.option("--llm/--no-llm", default=False, show_default=True, help="Use an LLM reviewer in addition to deterministic heuristics.")
@click.option("--save/--no-save", default=True, show_default=True, help="Write de_ai_report.json and review.md artifacts.")
def review_de_ai_command(
    asset_set_dir: Path,
    platform: str,
    content_format: str,
    env_file: str | None,
    provider: str | None,
    llm: bool,
    save: bool,
):
    """Run a platform-aware de-AI review for one generated artifact."""
    config = Config.from_env(env_file)
    run_record, run_path = start_run(
        config.output_dir,
        kind="de-ai-review",
        parameters={
            "asset_set_dir": str(asset_set_dir),
            "platform": platform,
            "format": content_format,
            "llm": llm,
            "provider": provider,
            "save": save,
        },
        files=[asset_set_dir],
    )
    try:
        report, report_path, review_path = review_de_ai(
            asset_set_dir,
            platform=platform,
            content_format=content_format,
            output_root=config.output_dir,
            env_file=env_file,
            provider_name=provider,
            use_llm=llm,
            save=save,
        )
    except Exception as e:
        finish_run(
            run_path,
            status="failed",
            failure_reason=str(e),
            resume_instruction="Fix the artifact path, provider configuration, or review target and rerun the command.",
            next_safe_action="Run `cmsbot review de-ai` again with the same asset set after resolving the failure.",
        )
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    changed = [path for path in (report_path, review_path) if path is not None]
    finish_run(
        run_path,
        status="completed",
        files_changed=changed,
        cost_summary={"llm_review": llm},
        summary=f"Reviewed {report['asset_set_id']} for {platform}/{content_format}.",
        next_safe_action=(
            "Human review required before approval."
            if report.get("requires_human_review")
            else "Artifact can proceed to approval review."
        ),
    )
    console.print(
        Panel.fit(
            f"[bold]De-AI Review Complete[/bold]\n\n"
            f"[dim]Asset Set:[/dim] {report['asset_set_id']}\n"
            f"[dim]Platform:[/dim]  {report['platform']}\n"
            f"[dim]Format:[/dim]    {report['format']}\n"
            f"[dim]Score:[/dim]     {report['score']}\n"
            f"[dim]Risk:[/dim]      {report['risk_level']}\n"
            f"[dim]Human:[/dim]     {report['requires_human_review']}\n"
            f"[dim]Report:[/dim]    {report_path or 'not saved'}\n"
            f"[dim]Review:[/dim]    {review_path or 'not saved'}\n"
            f"[dim]Run:[/dim]       {run_record['run_id']}",
            border_style="yellow",
        )
    )


@review.command("gates")
@click.argument("asset_set_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json-output", is_flag=True, help="Print raw gate JSON instead of a table.")
def review_gates(asset_set_dir: Path, json_output: bool):
    """Show deterministic review-gate status for an asset set."""
    payload = evaluate_asset_gates(asset_set_dir)
    if json_output:
        console.print(json.dumps(payload, indent=2))
        return

    table = Table(title=f"Review Gates ({payload['asset_set_id']})")
    table.add_column("Gate", style="bold")
    table.add_column("Status")
    table.add_column("Reason")
    for name, gate in payload["gates"].items():
        status = "[green]passed[/green]" if gate["passed"] else "[yellow]blocked[/yellow]"
        table.add_row(name, status, str(gate["reason"]))
    console.print(table)


@review.command("approve")
@click.argument("asset_set_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--approved-by", required=True, help="Name or handle of the human approver.")
@click.option("--notes", default=None, help="Optional approval notes.")
def review_approve(asset_set_dir: Path, approved_by: str, notes: str | None):
    """Record explicit human approval for an asset set."""
    payload, approval_path = record_human_approval(asset_set_dir, approved_by=approved_by, notes=notes)
    console.print(
        Panel.fit(
            f"[bold]Human Approval Recorded[/bold]\n\n"
            f"[dim]Approved By:[/dim] {payload['approved_by']}\n"
            f"[dim]Approval:[/dim]    {approval_path}",
            border_style="green",
        )
    )


@main.group()
def analytics():
    """Record and review cross-platform performance keyed to asset sets."""
    pass


@analytics.command("log-social")
@click.option("--asset-set-id", required=True, help="Asset set id, usually from manifest.json.")
@click.option("--platform", required=True, help="Platform name such as x, instagram, linkedin, or tiktok.")
@click.option("--format", "content_format", required=True, help="Format such as thread, carousel, or video_script.")
@click.option("--slot-id", default=None, help="Optional slot id from content-calendar.json.")
@click.option("--pillar", default=None, help="Optional pillar label or key.")
@click.option("--topic", default=None, help="Optional topic for the asset set.")
@click.option("--hook", default=None, help="Optional hook or opener used in the asset.")
@click.option("--source-blog", default=None, help="Optional source blog slug or title.")
@click.option("--posted-date", default=None, help="Publish date in YYYY-MM-DD format.")
@click.option("--views-24h", type=int, default=0, show_default=True)
@click.option("--views-48h", type=int, default=0, show_default=True)
@click.option("--views-7d", type=int, default=0, show_default=True)
@click.option("--impressions", type=int, default=0, show_default=True)
@click.option("--saves", type=int, default=0, show_default=True)
@click.option("--shares", type=int, default=0, show_default=True)
@click.option("--comments", type=int, default=0, show_default=True)
@click.option("--profile-visits", type=int, default=0, show_default=True)
@click.option("--link-clicks", type=int, default=0, show_default=True)
@click.option("--conversions", type=int, default=0, show_default=True)
@click.option("--performance-score", type=float, default=None, help="Optional explicit score override.")
@click.option("--notes", default=None, help="Optional notes about the performance snapshot.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def analytics_log_social(
    asset_set_id: str,
    platform: str,
    content_format: str,
    slot_id: str | None,
    pillar: str | None,
    topic: str | None,
    hook: str | None,
    source_blog: str | None,
    posted_date: str | None,
    views_24h: int,
    views_48h: int,
    views_7d: int,
    impressions: int,
    saves: int,
    shares: int,
    comments: int,
    profile_visits: int,
    link_clicks: int,
    conversions: int,
    performance_score: float | None,
    notes: str | None,
    env_file: str | None,
):
    """Log social performance against an asset set and optional distribution slot."""
    config = Config.from_env(env_file)
    record, destination = record_social_metrics(
        config.output_dir,
        asset_set_id=asset_set_id,
        platform=platform,
        format=content_format,
        slot_id=slot_id,
        pillar=pillar,
        topic=topic,
        hook=hook,
        source_blog=source_blog,
        posted_date=posted_date,
        views_24h=views_24h,
        views_48h=views_48h,
        views_7d=views_7d,
        impressions=impressions,
        saves=saves,
        shares=shares,
        comments=comments,
        profile_visits=profile_visits,
        link_clicks=link_clicks,
        conversions=conversions,
        performance_score=performance_score,
        notes=notes,
    )
    console.print(
        Panel.fit(
            f"[bold]Analytics Logged[/bold]\n\n"
            f"[dim]Asset Set:[/dim] {record['asset_set_id']}\n"
            f"[dim]Slot:[/dim]      {record.get('slot_id') or 'n/a'}\n"
            f"[dim]Platform:[/dim]  {record['platform']}\n"
            f"[dim]Format:[/dim]    {record['format']}\n"
            f"[dim]Score:[/dim]     {record['performance_score']}\n"
            f"[dim]Index:[/dim]     {destination}",
            border_style="green",
        )
    )


@analytics.command("import-seo-eval")
@click.option("--file", "report_file", type=click.Path(exists=True, path_type=Path), required=True, help="SEO evaluation report JSON.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def analytics_import_seo_eval(report_file: Path, env_file: str | None):
    """Import SEO evaluator output into the shared analytics index."""
    config = Config.from_env(env_file)
    try:
        imported, destination = import_seo_evaluation_report(config.output_dir, report_file)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]SEO Analytics Imported[/bold]\n\n"
            f"[dim]Records:[/dim] {imported}\n"
            f"[dim]Index:[/dim]   {destination}",
            border_style="blue",
        )
    )


@analytics.command("import-social-csv")
@click.option("--file", "csv_file", type=click.Path(exists=True, path_type=Path), required=True, help="Social analytics CSV export.")
@click.option("--platform", required=True, help="Fallback platform for rows without a platform column.")
@click.option("--format", "content_format", required=True, help="Fallback format for rows without a format column.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def analytics_import_social_csv(csv_file: Path, platform: str, content_format: str, env_file: str | None):
    """Import social analytics from a flexible CSV export."""
    config = Config.from_env(env_file)
    try:
        imported, destination = import_social_csv(
            config.output_dir,
            csv_file,
            platform=platform,
            format=content_format,
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    console.print(
        Panel.fit(
            f"[bold]Social CSV Imported[/bold]\n\n"
            f"[dim]Rows:[/dim]  {imported}\n"
            f"[dim]Index:[/dim] {destination}",
            border_style="green",
        )
    )


@analytics.command("report")
@click.option("--days", type=int, default=30, show_default=True, help="Lookback window in days.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
def analytics_report(days: int, env_file: str | None):
    """Show a high-level analytics summary from the shared performance index."""
    config = Config.from_env(env_file)
    records = load_performance_index(config.output_dir)
    if not records:
        console.print("[dim]No analytics records found yet.[/dim]")
        return

    report = render_performance_report(records, lookback_days=days)
    totals = report["totals"]
    console.print(
        Panel.fit(
            f"[bold]Analytics Report[/bold]\n\n"
            f"[dim]Lookback:[/dim]    {days} days\n"
            f"[dim]Records:[/dim]     {totals['records']}\n"
            f"[dim]Views (7d):[/dim]  {totals['views_7d']:,}\n"
            f"[dim]Impressions:[/dim] {totals['impressions']:,}\n"
            f"[dim]Saves:[/dim]       {totals['saves']:,}\n"
            f"[dim]Conversions:[/dim] {totals['conversions']:,}",
            border_style="magenta",
        )
    )

    signals = report["signals"]
    if signals.get("top_formats"):
        table = Table(title="Top Formats")
        table.add_column("Format", style="bold")
        table.add_column("Avg Score", justify="right")
        table.add_column("Count", justify="right")
        for item in signals["top_formats"]:
            table.add_row(item["key"], f"{item['average_score']:.2f}", str(item["count"]))
        console.print(table)

    if signals.get("top_pillars"):
        table = Table(title="Top Pillars")
        table.add_column("Pillar", style="bold")
        table.add_column("Avg Score", justify="right")
        table.add_column("Count", justify="right")
        for item in signals["top_pillars"]:
            table.add_row(item["key"], f"{item['average_score']:.2f}", str(item["count"]))
        console.print(table)


@main.group()
def calendar():
    """Plan and inspect recurring content calendar entries."""
    pass


@calendar.command("plan")
@click.option("--project-file", type=click.Path(exists=True), required=True, help="CMSBOT project profile JSON.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--days", type=int, default=None, help="Planning horizon in days.")
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Calendar start date in YYYY-MM-DD format. Defaults to today.",
)
@click.option(
    "--save",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional explicit output path. Defaults to <CMSBOT_OUTPUT_DIR>/calendar/content-calendar.json.",
)
def calendar_plan(
    project_file: str,
    env_file: str | None,
    days: int | None,
    start_date: datetime | None,
    save: Path | None,
):
    """Generate a calendar file from the project's content_ops configuration."""
    config = Config.from_env(env_file)
    profile = load_project_profile(project_file)
    content_ops = profile.section("content_ops")
    cadence = content_ops.get("cadence", {}) if isinstance(content_ops.get("cadence"), dict) else {}

    chosen_days = days or int(cadence.get("planning_horizon_days", 30) or 30)
    chosen_start = start_date.date() if start_date else date.today()
    analytics_records = load_performance_index(config.output_dir)

    payload = build_content_calendar(
        profile.name,
        profile.slug,
        content_ops=content_ops,
        seo=profile.section("seo"),
        start_date=chosen_start,
        days=chosen_days,
        analytics_records=analytics_records,
    )

    if save:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        destination = save
    else:
        destination = write_content_calendar(config.output_dir, payload)

    console.print(
        Panel.fit(
            f"[bold]Content Calendar Planned[/bold]\n\n"
            f"[dim]Project:[/dim] {profile.name}\n"
            f"[dim]Start:[/dim]   {payload['start_date']}\n"
            f"[dim]Days:[/dim]    {payload['days']}\n"
            f"[dim]Entries:[/dim] {len(payload['entries'])}\n"
            f"[dim]Output:[/dim]  {destination}",
            border_style="blue",
        )
    )


@calendar.command("show")
@click.option("--file", "calendar_file", type=click.Path(exists=True, path_type=Path), default=None, help="Calendar JSON file.")
@click.option("--env-file", type=click.Path(exists=True), default=None, help="Path to .env file.")
@click.option("--limit", type=int, default=12, show_default=True, help="Maximum entries to show.")
def calendar_show(calendar_file: Path | None, env_file: str | None, limit: int):
    """Show planned content entries from a generated calendar file."""
    config = Config.from_env(env_file)
    chosen_file = calendar_file or (config.output_dir / "calendar" / "content-calendar.json")
    if not chosen_file.exists():
        console.print(f"[red]Error:[/red] No calendar found at {chosen_file}")
        sys.exit(1)

    payload = load_content_calendar(chosen_file)
    entries = payload.get("entries", [])
    if not entries:
        console.print(f"[dim]No entries found in {chosen_file}.[/dim]")
        return

    table = Table(title=f"Content Calendar ({chosen_file})")
    table.add_column("Date", style="bold")
    table.add_column("Pillar")
    table.add_column("Topic")
    table.add_column("Assets")
    table.add_column("Slots", justify="right")

    for entry in entries[:limit]:
        assets = ", ".join(entry.get("asset_set", []))
        table.add_row(
            str(entry.get("date", "")),
            str(entry.get("pillar_label") or entry.get("pillar") or ""),
            str(entry.get("topic") or ""),
            assets,
            str(len(entry.get("distribution_slots", []))),
        )

    console.print(table)
    console.print(f"\n[dim]Showing {min(limit, len(entries))} of {len(entries)} entries.[/dim]")


@main.group()
def routing():
    """Inspect model-routing policy and escalation decisions."""
    pass


@routing.command("show")
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repository or project root containing strategy/model-routing.json.",
)
def routing_show(root: Path):
    """Print configured model-routing policy."""
    payload = load_model_routing(root)
    console.print(json.dumps(payload, indent=2))


@routing.command("decide")
@click.argument("work_type")
@click.option(
    "--trigger",
    "triggers",
    multiple=True,
    help="Active escalation trigger. Can be provided more than once.",
)
@click.option(
    "--root",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Repository or project root containing strategy/model-routing.json.",
)
def routing_decide(work_type: str, triggers: tuple[str, ...], root: Path):
    """Show the deterministic route for one work type and trigger set."""
    policy = load_model_routing(root)
    decision = decide_model_route(policy, work_type=work_type, triggers=list(triggers))
    console.print(
        json.dumps(
            {
                "work_type": decision.work_type,
                "tier": decision.tier,
                "escalate": decision.escalate,
                "matched_triggers": decision.matched_triggers,
                "policy": decision.policy,
            },
            indent=2,
        )
    )


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


def _print_results(
    result,
    budget,
    *,
    asset_set_dir: Path,
    source_dir: Path,
    manifest_path: Path | None = None,
):
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

    console.print(f"\n[green bold]Source HTML:[/green bold] {source_dir / 'index.html'}")
    if manifest_path is not None:
        console.print(f"[green bold]Manifest:[/green bold] {manifest_path}")
    if asset_set_dir != source_dir:
        console.print(f"[green bold]Asset Set:[/green bold] {asset_set_dir}")
    console.print()
if __name__ == "__main__":
    main()
