# CMSBOT skills

Each folder is one skill: a `SKILL.md` with a name, a description of when to use it, and the method.
They follow the open Agent Skills layout, so Claude Code, the Claude desktop app and other harnesses
that read `SKILL.md` folders can load them. The dependable way to install them is to copy or link
the folders into a skills directory the harness already reads:

```bash
ln -s "$PWD"/skills/cmsbot-* ~/.claude/skills/
```

The repo root is also laid out as a Claude Code plugin (`.claude-plugin/plugin.json`, `skills/`,
`.mcp.json`), so it can be loaded as one; check the current Claude Code plugin docs for the install
command. For an agent that has no skills folder (Grok Bot), paste the body of the skill it needs
into that agent's instructions; `cmsbot-distribute` is written to be used that way.

## Adding a skill

1. Copy the skill folder (yours, or one someone published) into `skills/`.
2. Make sure its `SKILL.md` front matter has `name` and `description`.
3. Add one line to the method telling it where its output goes: drafts are saved with the Hub tool
   `save_draft` and handed over with `submit_for_review`; money spent is logged with `record_cost`.

That is the whole contract. A skill never needs to know about other skills, the review page, or who
posts. It reads `get_brief`, does its work, and saves a draft. If it needs an outside tool (an image
or video model, a search API), it says so under "Needs" and reads the key from the environment.

## What is here

| Skill | Use it to |
|---|---|
| `cmsbot-onboard` | Set up a new project: brand kit, pillars, channels, budget |
| `cmsbot-plan` | Plan the next period from pillars, trend signals and what has worked |
| `cmsbot-write` | Draft an anchor piece and its per-channel derivatives, or revise after review |
| `cmsbot-short-video` | Produce a 20-75 second vertical video from an approved script |
| `cmsbot-distribute` | Post approved items and report results (written for any posting agent) |
| `cmsbot-analyze` | Pull results, attribute traffic, propose changes to the method |
