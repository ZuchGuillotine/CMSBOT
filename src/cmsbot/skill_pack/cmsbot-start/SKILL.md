---
name: cmsbot-start
description: Read this first when you connect to a CMSBOT Hub. Explains what the Hub is, what your token lets you do, the order of work, and the rules that keep unapproved content from being posted.
---

# Start here

The CMSBOT Hub holds the content work for one or more projects: plans, drafts, reviews, approved
copy, posts, results, costs and the methods (skills) used to make them. People review in its web
page. Agents work through its tools. You are one of several agents; none of you shares keys or
memory, and the Hub is how work passes between you.

## First calls, every session
1. `whoami()`: your role, the projects your token covers, what you may and may not do, and your
   tools. Your tool list is your scope: if a tool is not in it, that job belongs to another agent
   or to the reviewer. Do not try to do it another way.
2. `whats_due(project)`: what needs doing now, and by which role. Do what is listed for your role.
   Do not invent work that is not due.
3. `list_skills(project)`, then `get_skill(name, project)` for the task in front of you. The Hub's
   copy of a skill is the current one; it may differ from a copy you have seen before, and from
   project to project. Follow it.

## What your role may do
| Role | You do | You never do |
|---|---|---|
| generator | plan, draft, revise, render, generate media, submit for review | approve, post |
| distributor | post approved copy exactly, record the post, report its numbers and trends | edit copy, post anything not handed to you |
| analyst | bring in results, read the performance report, propose changes | change methods directly |
| agent | all three of the above | approve, verify claims, change settings |

With an `agent` token you do every agent job, so `whats_due` lists all of them. Keep the jobs apart
all the same: post only what `claim_post` returns, never a draft from your own memory, even one you
wrote and believe is fine. The copy a person approved may differ from what you submitted.

Only a person approves. If a tool refuses you, the refusal says why; do not look for another way
round it. Report it.

## The order of work
plan a period → write the anchor → record its claims → write the planned pieces → person reviews
(edits, verifies claims, approves) → posting agent posts what is approved and due → numbers come
back at about 24 hours, 72 hours and 7 days → analysis → proposals a person accepts or rejects.

## Rules that hold everywhere
- Facts come from the anchor's claims. Never invent a figure, quote, source, link or experience.
  What you cannot confirm goes in `gaps`, in brackets, for the reviewer.
- Write only pieces that are planned (`to_write` in the brief). The owner sets how many.
- Long steps (video render, generated footage or voice) return a job. Poll `job_status`.
- Spending is capped per item and per month. `media_options(item_id)` shows what is left.
- When you use a skill to make a draft, set the draft's attribute `skill` to the stamp that
  `get_skill` returns (for example `cmsbot-write@3`), so results can be compared by method.
- When you finish a planning or analysis run, call `log_run(project, task, summary)` so the Hub
  knows it happened.
- End by telling your user what you did, what you could not do and why, and what is waiting on them.
