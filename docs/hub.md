# CMSBOT Hub

One small service that holds content state for every project and is the single place where people
review, agents hand work over, and results come back.

```
 drafting agents (Claude, Codex)          people                 posting agents (Grok Bot, ...)
   get_brief, save_draft,          review page: edit inline,       ready_to_post, claim_post,
   set_claims, submit_for_review   verify claims, approve          mark_posted, report_metrics,
            │                              │                        submit_signal
            └──────────────┬───────────────┴───────────────┬────────────────┘
                           ▼                               ▼
                     CMSBOT Hub  (one SQLite file: projects, pillars, clusters, items, revisions,
                                  claims, posts, metrics, conversions, costs, signals, learnings)
                           ▲
            analysis agents: report_conversions, performance, propose_learning
```

## Rules it enforces

- Agents never approve. Only a reviewer token can approve, edit after submission, or verify claims.
- Approval is tied to the exact copy. Any later edit withdraws it. The reviewer can fix a word and
  approve in one step, so small corrections don't go back to an agent.
- A posting agent sees only approved copy that is due, takes it with a claim so nothing is posted
  twice, and gets the link with tracking tags already added (`utm_content=cmsbot-<id>`).
- Gaps (facts the agent could not confirm) and, where the project requires it, unverified claims
  block approval.
- A change to the method (a "learning") reaches drafting agents only after a person accepts it.

## Run it

```bash
pip install -e ".[hub]"
cmsbot hub init --reviewer "your name"
```

```bash
cmsbot hub serve
```

Open http://127.0.0.1:8787 and paste the reviewer token. Then, in another shell:

```bash
export CMSBOT_HUB_TOKEN=<reviewer token>
cmsbot hub project projects/example-project.json --budget 100
```

```bash
cmsbot hub token create claude-weekly --role generator
```

```bash
cmsbot hub token create grok-bot --role distributor
```

```bash
cmsbot hub token create analyst --role analyst
```

Send existing pipeline output for review (safe to re-run):

```bash
cmsbot hub push output/asset-sets/2026/2026-11/<asset-set-id> --project <slug> --link https://site/blog/<slug>
```

## Host it

It is one process and one file, shared by all projects, so it needs the smallest instance a host
sells plus a persistent disk for `/data`. That is typically a few dollars a month in total, not per
project; check your host's current prices. Nothing else is required: no managed database, no queue.

```bash
docker build -t cmsbot-hub .
```

```bash
docker run -p 8787:8787 -v cmsbot-hub-data:/data cmsbot-hub
```

Serve it over HTTPS (the host's built-in TLS is enough). Back up `/data/hub.sqlite3`.
It can run on a laptop while you evaluate it; cloud agents such as Grok Bot can only reach it once
it has a public HTTPS address.

## Connect agents

MCP endpoint: `https://<your-hub>/mcp`, with the header `Authorization: Bearer <token>`.
Clients that can store only a URL can use `https://<your-hub>/mcp?token=<token>`.

Claude Code:

```bash
claude mcp add --transport http cmsbot-hub https://<your-hub>/mcp --header "Authorization: Bearer <generator token>"
```

Grok Bot: add a custom connector of type "Remote HTTPS" with that URL and the header carrying the
distributor token, then give it the text of `skills/cmsbot-distribute/SKILL.md` as its instructions.

Give each agent its own token and the narrowest role. `--project <slug>` limits a token to one project.

## REST

Everything the MCP tools do is also under `/api` with the same token (interactive reference at
`/api/docs`). `cmsbot hub brief <project>` and `cmsbot hub ready` are thin wrappers for scripts.

## How results are judged

`performance` (MCP) or the Insights tab compares posts in a way that avoids the usual traps:

| Trap | Guard |
|---|---|
| New posts look worse because they are new | Each post is judged on the snapshot nearest a fixed age (7 days by default); younger posts wait |
| Channels and seasons differ | Each post is compared with the median of other posts on the same channel within 28 days |
| One lucky post rewrites the strategy | Averages are pulled toward "typical" until a value has enough posts; under 5 is "not enough data" |
| Winners crowd out everything else | The suggested mix keeps a floor for every option |

Report metrics at about 24h, 72h and 7d. Site results come from GA4 or Mixpanel grouped by
`utm_content` and are sent with `report_conversions`.
