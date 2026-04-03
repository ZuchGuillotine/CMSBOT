# CMSBOT SEO Toolkit

Project-driven keyword research and content evaluation for CMSBOT.

## What it does

1. `npm run research`
   Pulls keyword ideas from Google Autocomplete, Reddit, and competitor sitemaps.
2. `npm run evaluate`
   Joins Google Search Console, GA4, and your published content to surface quick wins, content gaps, decliners, and topic briefs.

Both workflows are driven by `CMSBOT_PROJECT_FILE`, which should point at a project profile JSON file such as [`projects/example-project.json`](../projects/example-project.json).

## Setup

```bash
cd seo
npm install
export CMSBOT_PROJECT_FILE=../projects/example-project.json
```

Optional analytics overrides:

```bash
export GOOGLE_SERVICE_ACCOUNT_KEY_PATH=/path/to/service-account.json
export GSC_SITE_URL=sc-domain:example.com
export GA4_PROPERTY_ID=123456789
export BLOG_API_URL=https://example.com/api/blog
export GA4_CONVERSION_EVENT=generate_lead
```

## Usage

```bash
npm run research
npm run evaluate
npm run evaluate -- --days 14
```

Output lands in `seo/output/`:

- `keywords_YYYY-MM-DD.json`
- `keywords_YYYY-MM-DD.csv`
- `evaluation_YYYY-MM-DD.json`
- `evaluation_YYYY-MM-DD.csv`
- `topic_briefs_YYYY-MM-DD.json`

Use the briefs with CMSBOT:

```bash
cmsbot topics --project-file projects/example-project.json
cmsbot topics --command 0 --project-file projects/example-project.json
```
