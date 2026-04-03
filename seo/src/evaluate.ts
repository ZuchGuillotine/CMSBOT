import { writeFile, mkdir, readFile, readdir } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CONVERSION_EVENT_NAME, EVALUATION_DAYS, COMPARE_DAYS } from './config.js';
import { getKeywordRankings, getPagePerformance, getTopMovers, type GSCKeywordData, type GSCPageData } from './gsc.js';
import { getBlogTraffic, getConversionsByContent, type GA4PageData } from './ga4.js';
import {
  fetchPublishedPosts,
  buildContentScorecards,
  findContentGaps,
} from './contentPerformance.js';
import { evaluate } from './evaluator.js';
import type { ScoredKeyword } from './scorer.js';
import { generateEvaluationCSV } from './utils.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = process.env.CMSBOT_SEO_OUTPUT_DIR
  ? resolve(process.env.CMSBOT_SEO_OUTPUT_DIR)
  : join(__dirname, '..', 'output');

const args = process.argv.slice(2);
const gscOnly = args.includes('--gsc-only');
const ga4Only = args.includes('--ga4-only');
const generateBriefs = args.includes('--generate-briefs') || (!gscOnly && !ga4Only);
const runAll = !gscOnly && !ga4Only;

// Parse --days flag
const daysIdx = args.indexOf('--days');
const days = daysIdx >= 0 ? parseInt(args[daysIdx + 1]) || EVALUATION_DAYS : EVALUATION_DAYS;
const compareDays = COMPARE_DAYS;

async function loadLatestSEOKeywords(): Promise<ScoredKeyword[]> {
  try {
    const files = await readdir(OUTPUT_DIR);
    const keywordFiles = files
      .filter(f => f.startsWith('keywords_') && f.endsWith('.json'))
      .sort()
      .reverse();

    if (keywordFiles.length === 0) {
      console.log('[Evaluate] No SEO keyword research files found. Run "npm run research" first.');
      return [];
    }

    const latest = keywordFiles[0];
    console.log(`[Evaluate] Loading SEO keywords from ${latest}`);
    const data = JSON.parse(await readFile(join(OUTPUT_DIR, latest), 'utf-8'));
    return data.allKeywords || [];
  } catch {
    console.log('[Evaluate] Could not load SEO keyword data');
    return [];
  }
}

async function main() {
  console.log('=== SEO Evaluation Loop ===');
  console.log(`Period: last ${days} days (comparing to prior ${compareDays} days)`);
  console.log(`Started at ${new Date().toISOString()}\n`);

  await mkdir(OUTPUT_DIR, { recursive: true });

  // Load SEO keyword research data
  const seoKeywords = await loadLatestSEOKeywords();

  // Fetch GSC data
  let gscKeywords: GSCKeywordData[] = [];
  let gscPages: GSCPageData[] = [];
  let movers: Awaited<ReturnType<typeof getTopMovers>> = [];

  if (runAll || gscOnly) {
    try {
      [gscKeywords, gscPages, movers] = await Promise.all([
        getKeywordRankings(days),
        getPagePerformance(days),
        getTopMovers(days, compareDays),
      ]);
    } catch (err) {
      console.error(`[GSC] Error: ${(err as Error).message}`);
      if (gscOnly) {
        process.exit(1);
      }
      console.log('[GSC] Continuing without GSC data...\n');
    }
  }

  // Fetch GA4 data
  let ga4Pages: GA4PageData[] = [];

  if (runAll || ga4Only) {
    try {
      ga4Pages = await getBlogTraffic(days);

      // Enrich with conversion data
      const conversions = await getConversionsByContent(days, CONVERSION_EVENT_NAME);
      for (const page of ga4Pages) {
        page.conversions = conversions.get(page.path) || 0;
      }
    } catch (err) {
      console.error(`[GA4] Error: ${(err as Error).message}`);
      if (ga4Only) {
        process.exit(1);
      }
      console.log('[GA4] Continuing without GA4 data...\n');
    }
  }

  // Fetch published blog posts and build scorecards
  const posts = await fetchPublishedPosts();
  const scorecards = buildContentScorecards(posts, gscKeywords, gscPages, ga4Pages);

  // Find content gaps
  const contentGaps = findContentGaps(seoKeywords, gscKeywords, scorecards);

  // Run evaluation
  const report = evaluate(
    gscKeywords,
    scorecards,
    contentGaps,
    movers,
    days,
    compareDays
  );

  // Write outputs
  const timestamp = new Date().toISOString().split('T')[0];

  await writeFile(
    join(OUTPUT_DIR, `evaluation_${timestamp}.json`),
    JSON.stringify(report, null, 2)
  );

  await writeFile(
    join(OUTPUT_DIR, `evaluation_${timestamp}.csv`),
    generateEvaluationCSV(report)
  );

  if (generateBriefs && report.topicBriefs.length > 0) {
    await writeFile(
      join(OUTPUT_DIR, `topic_briefs_${timestamp}.json`),
      JSON.stringify(report.topicBriefs, null, 2)
    );
  }

  // Print console report
  console.log('\n========================================');
  console.log('         EVALUATION REPORT');
  console.log('========================================\n');

  console.log(`Period: ${days} days | Compare: ${compareDays} days prior`);
  console.log(`Keywords tracked: ${report.summary.totalKeywordsTracked}`);
  console.log(`Content pieces:   ${report.summary.totalContentPieces}`);
  console.log(`Avg position:     ${report.summary.avgPosition.toFixed(1)}`);
  console.log(`Total impressions: ${report.summary.totalImpressions.toLocaleString()}`);
  console.log(`Total clicks:     ${report.summary.totalClicks.toLocaleString()}`);
  console.log(`Overall CTR:      ${(report.summary.overallCTR * 100).toFixed(2)}%`);

  if (report.quickWins.length > 0) {
    console.log(`\n--- Quick Wins (${report.quickWins.length}) ---`);
    for (const qw of report.quickWins.slice(0, 5)) {
      console.log(
        `  pos ${qw.position.toFixed(1).padEnd(6)} ${qw.impressions.toString().padEnd(8)} imp  "${qw.keyword}"`
      );
    }
    if (report.quickWins.length > 5)
      console.log(`  ... and ${report.quickWins.length - 5} more`);
  }

  if (report.ctrOpportunities.length > 0) {
    console.log(`\n--- CTR Opportunities (${report.ctrOpportunities.length}) ---`);
    for (const ctr of report.ctrOpportunities.slice(0, 5)) {
      console.log(
        `  pos ${ctr.position.toFixed(1).padEnd(6)} CTR ${(ctr.currentCTR * 100).toFixed(1)}% → ${(ctr.expectedCTR * 100).toFixed(0)}%  "${ctr.keyword}"`
      );
    }
  }

  if (report.contentGaps.length > 0) {
    console.log(`\n--- Content Gaps (${report.contentGaps.length}) ---`);
    for (const gap of report.contentGaps.slice(0, 5)) {
      console.log(
        `  score ${gap.seoScore.toFixed(1).padEnd(6)} [${gap.category}]  "${gap.keyword}"`
      );
    }
    if (report.contentGaps.length > 5)
      console.log(`  ... and ${report.contentGaps.length - 5} more`);
  }

  if (report.decliners.length > 0) {
    console.log(`\n--- Declining Keywords (${report.decliners.length}) ---`);
    for (const d of report.decliners.slice(0, 5)) {
      console.log(
        `  ${d.previousPosition.toFixed(1)} → ${d.currentPosition.toFixed(1)} (${d.positionDelta > 0 ? '+' : ''}${d.positionDelta.toFixed(1)})  "${d.keyword}"`
      );
    }
  }

  if (report.topicBriefs.length > 0) {
    console.log(`\n--- Auto-Generated Topic Briefs (${report.topicBriefs.length}) ---`);
    for (const brief of report.topicBriefs.slice(0, 5)) {
      console.log(`  P${brief.priority} [${brief.cluster}] "${brief.title_seed}"`);
    }
  }

  console.log(`\nReports saved to:`);
  console.log(`  JSON:   ${join(OUTPUT_DIR, `evaluation_${timestamp}.json`)}`);
  console.log(`  CSV:    ${join(OUTPUT_DIR, `evaluation_${timestamp}.csv`)}`);
  if (generateBriefs && report.topicBriefs.length > 0) {
    console.log(`  Briefs: ${join(OUTPUT_DIR, `topic_briefs_${timestamp}.json`)}`);
  }
}

main().catch(console.error);
