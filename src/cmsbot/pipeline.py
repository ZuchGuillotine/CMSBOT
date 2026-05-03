"""Autoreason pipeline — the main orchestration loop."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from cmsbot.agents.author import AuthorAgent
from cmsbot.agents.candidates import RewriterAgent, SynthesizerAgent
from cmsbot.agents.image_judge import ImageJudge
from cmsbot.agents.images import ImageDirector
from cmsbot.agents.judge import JudgePanel
from cmsbot.agents.strawman import StrawmanAgent
from cmsbot.budget import BudgetTracker
from cmsbot.config import Config
from cmsbot.models import ContentType, Draft, PipelineResult, RoundResult
from cmsbot.providers import build_provider
from cmsbot.providers.image_providers import (
    GPTImageProvider,
    GrokImageProvider,
    ImageProvider,
    NanobananProvider,
    UnsplashProvider,
)
from cmsbot.providers.openai_provider import OpenAIProvider
from cmsbot.renderer import Renderer


class AutoreasonPipeline:
    """
    Multi-agent content refinement using the Autoreason pattern.

    Flow per round:
        1. Author A writes initial draft (round 1 only)
        2. Strawman attacks the draft — problems only, no fixes
        3. Three candidates generated:
           A·keep  = original draft unchanged
           B·rewrite = fresh rewrite using task + A + critique
           AB·synthesize = merges best of A and B
        4. Blind judge panel (3 fresh agents, Borda count)
        5. Winner becomes new A; loop until streak=2 or budget/rounds exhausted
    """

    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self.budget = BudgetTracker(cap=self.config.budget_cap)
        self._log: Callable[[str], None] = lambda msg: None  # overridden by CLI

    def set_logger(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    def _build_agents(self) -> None:
        """Instantiate all agents from config."""
        roles = self.config.roles

        # Core agents
        self.author = AuthorAgent(build_provider(roles.author, self.config))
        self.strawman = StrawmanAgent(build_provider(roles.strawman, self.config))
        self.rewriter = RewriterAgent(build_provider(roles.rewriter, self.config))
        self.synthesizer = SynthesizerAgent(build_provider(roles.synthesizer, self.config))

        # Judge panel — 3 diverse providers
        judge_providers = []
        for name in roles.judges:
            judge_providers.append(build_provider(name, self.config))
        self.judge_panel = JudgePanel(judge_providers)

        # Image providers — build all available, GPT Image as fallback
        director_provider = build_provider(roles.image_director, self.config)
        raster_image_providers: list[ImageProvider] = []
        fetch_provider: ImageProvider | None = None

        if self.config.xai_key:
            raster_image_providers.append(
                GrokImageProvider(api_key=self.config.xai_key)
            )

        if self.config.gemini:
            raster_image_providers.append(
                NanobananProvider(api_key=self.config.gemini.api_key)
            )

        if self.config.unsplash_key:
            fetch_provider = UnsplashProvider(access_key=self.config.unsplash_key)

        # GPT Image as fallback if fewer than 2 providers available
        if len(raster_image_providers) < 2 and self.config.openai:
            raster_image_providers.append(
                GPTImageProvider(
                    api_key=self.config.openai.api_key,
                    quality="high",
                )
            )

        if not raster_image_providers and fetch_provider is None:
            raise ValueError(
                "No image providers configured. Set at least one of: "
                "XAI_API_KEY, GEMINI_API_KEY, UNSPLASH_ACCESS_KEY, OPENAI_API_KEY"
            )

        # Image judge — use a vision-capable model (prefer GPT-4o for vision)
        if self.config.openai:
            judge_provider = OpenAIProvider(
                api_key=self.config.openai.api_key, model="gpt-4o"
            )
        elif self.config.anthropic:
            judge_provider = build_provider("anthropic", self.config)
        else:
            judge_provider = director_provider
        image_judge = ImageJudge(judge_provider)

        svg_provider = build_provider("openai", self.config) if self.config.openai else None
        svg_fallback_provider = build_provider("anthropic", self.config) if self.config.anthropic else None
        if svg_provider is None and svg_fallback_provider is not None:
            svg_provider = svg_fallback_provider
            svg_fallback_provider = None

        self.image_director = ImageDirector(
            director_provider=director_provider,
            raster_image_providers=raster_image_providers,
            fetch_provider=fetch_provider,
            judge=image_judge,
            budget=self.budget,
            svg_provider=svg_provider,
            svg_fallback_provider=svg_fallback_provider,
        )

        self.renderer = Renderer()

    async def run(
        self,
        task_prompt: str,
        content_type: str | ContentType = "blog",
        max_rounds: int | None = None,
        output_dir: Path | None = None,
    ) -> PipelineResult:
        """Run the full Autoreason pipeline."""
        if isinstance(content_type, str):
            content_type = ContentType(content_type)

        max_rounds = max_rounds or self.config.max_rounds
        output_dir = output_dir or self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        self._build_agents()

        rounds: list[RoundResult] = []
        current_draft: Draft | None = None
        streak = 0
        converged = False

        for round_num in range(1, max_rounds + 1):
            round_start_cost = self.budget.spent
            self._log(f"\n{'='*60}")
            self._log(f"ROUND {round_num}/{max_rounds}  |  Streak: {streak}/2  |  Spent: ${self.budget.spent:.4f}")
            self._log(f"{'='*60}")

            # Budget check before starting a round
            if not self.budget.can_afford(0.05):
                self._log("Budget nearly exhausted — stopping iteration.")
                break

            # --- Step 1: Author A (round 1 only) ---
            if current_draft is None:
                self._log("\n[Author A] Writing initial draft...")
                current_draft, resp = await self.author.write(task_prompt, content_type)
                current_draft.round_num = round_num
                self.budget.record("author", resp.provider, resp.model, resp.input_tokens, resp.output_tokens)
                self._log(f"  -> {resp.model} | {resp.input_tokens}+{resp.output_tokens} tokens | ${resp.cost:.4f}")

            # --- Step 2: Strawman critique ---
            self._log("\n[Strawman] Critiquing draft...")
            critique, resp = await self.strawman.critique(task_prompt, current_draft.content, content_type)
            critique.round_num = round_num
            self.budget.record("strawman", resp.provider, resp.model, resp.input_tokens, resp.output_tokens)
            self._log(f"  -> {resp.model} | {resp.input_tokens}+{resp.output_tokens} tokens | ${resp.cost:.4f}")

            # --- Step 3: Generate candidates ---
            # A·keep = current draft (no API call)
            candidate_a = Draft(
                content=current_draft.content,
                round_num=round_num,
                origin="keep",
                provider=current_draft.provider,
                model=current_draft.model,
            )

            # B·rewrite
            self._log("\n[B·Rewrite] Rewriting from scratch with critique...")
            candidate_b, resp_b = await self.rewriter.rewrite(
                task_prompt, current_draft.content, critique.problems, content_type
            )
            candidate_b.round_num = round_num
            self.budget.record("rewriter", resp_b.provider, resp_b.model, resp_b.input_tokens, resp_b.output_tokens)
            self._log(f"  -> {resp_b.model} | {resp_b.input_tokens}+{resp_b.output_tokens} tokens | ${resp_b.cost:.4f}")

            # AB·synthesize
            self._log("\n[AB·Synthesize] Merging best of A and B...")
            candidate_ab, resp_ab = await self.synthesizer.synthesize(
                task_prompt, current_draft.content, candidate_b.content, content_type
            )
            candidate_ab.round_num = round_num
            self.budget.record("synthesizer", resp_ab.provider, resp_ab.model, resp_ab.input_tokens, resp_ab.output_tokens)
            self._log(f"  -> {resp_ab.model} | {resp_ab.input_tokens}+{resp_ab.output_tokens} tokens | ${resp_ab.cost:.4f}")

            # --- Step 4: Blind judge panel ---
            self._log("\n[Judges] Blind evaluation (3 judges, Borda count)...")
            candidates = [candidate_a, candidate_b, candidate_ab]
            winner_idx, winner_label, judgments, judge_responses = await self.judge_panel.evaluate(
                candidates, task_prompt
            )

            for jr in judge_responses:
                self.budget.record("judge", jr.provider, jr.model, jr.input_tokens, jr.output_tokens)

            judge_cost = sum(jr.cost for jr in judge_responses)
            self._log(f"  -> 3 judges | ${judge_cost:.4f} total")
            self._log(f"  -> Winner: {winner_label}")

            winner = candidates[winner_idx]

            # --- Step 5: Convergence check ---
            if winner_label == "A":
                streak += 1
                self._log(f"\n  A wins again — streak={streak}")
            else:
                streak = 0
                self._log(f"\n  {winner_label} wins — streak reset to 0")

            current_draft = winner
            round_cost = self.budget.spent - round_start_cost

            rounds.append(RoundResult(
                round_num=round_num,
                draft_a=candidate_a,
                critique=critique,
                candidate_b=candidate_b,
                candidate_ab=candidate_ab,
                winner=winner,
                winner_label=winner_label,
                streak=streak,
                round_cost=round_cost,
            ))

            if streak >= 2:
                converged = True
                self._log(f"\n*** CONVERGED after {round_num} rounds (streak=2) ***")
                break

        # --- Step 6: Image generation (multi-candidate + judge) ---
        self._log(f"\n{'='*60}")
        self._log("[Image Director] Planning and generating images...")
        images, img_responses = await self.image_director.generate_images(
            task_prompt, current_draft.content, content_type, output_dir,
            log_fn=self._log,
        )
        for ir in img_responses:
            self.budget.record("image_director", ir.provider, ir.model, ir.input_tokens, ir.output_tokens)

        self._log(f"  -> {len(images)} images generated")

        # --- Step 7: Render HTML ---
        self._log("\n[Renderer] Building HTML output...")
        html = self.renderer.render(
            content=current_draft.content,
            images=images,
            content_type=content_type,
            task_prompt=task_prompt,
        )

        # Write output files
        markdown_path = output_dir / "content.md"
        markdown_path.write_text(current_draft.content, encoding="utf-8")
        self._log(f"  -> Saved markdown to {markdown_path}")

        html_path = output_dir / "index.html"
        html_path.write_text(html, encoding="utf-8")
        self._log(f"  -> Saved to {html_path}")

        # Summary
        self._log(f"\n{'='*60}")
        self._log("PIPELINE COMPLETE")
        self._log(f"  Rounds: {len(rounds)} | Converged: {converged}")
        self._log(f"  Total cost: ${self.budget.spent:.4f}")
        self._log(f"  Budget remaining: ${self.budget.remaining:.4f}")

        return PipelineResult(
            task_prompt=task_prompt,
            content_type=content_type,
            final_draft=current_draft,
            rounds=rounds,
            images=images,
            html=html,
            total_cost=self.budget.spent,
            total_rounds=len(rounds),
            converged=converged,
        )
