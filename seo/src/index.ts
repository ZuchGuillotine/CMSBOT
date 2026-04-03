import { writeFile, mkdir } from 'node:fs/promises';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { scrapeAutocomplete } from './googleAutocomplete.js';
import { mineReddit } from './redditMining.js';
import { analyzeCompetitors } from './competitorGap.js';
import { scoreKeywords } from './scorer.js';
import { generateCSV } from './utils.js';
import { SEED_KEYWORDS } from './config.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = process.env.CMSBOT_SEO_OUTPUT_DIR
  ? resolve(process.env.CMSBOT_SEO_OUTPUT_DIR)
  : join(__dirname, '..', 'output');

const args = process.argv.slice(2);
const autocompleteOnly = args.includes('--autocomplete-only');
const redditOnly = args.includes('--reddit-only');
const competitorsOnly = args.includes('--competitors-only');
const runAll = !autocompleteOnly && !redditOnly && !competitorsOnly;

async function main() {
  console.log('=== SEO Keyword Research Tool ===');
  console.log(`Started at ${new Date().toISOString()}\n`);

  if (SEED_KEYWORDS.length === 0) {
    console.warn('[Config] No SEO seed keywords loaded. Set CMSBOT_PROJECT_FILE to a populated project profile JSON.');
  }

  await mkdir(OUTPUT_DIR, { recursive: true });

  const autocompleteResults =
    runAll || autocompleteOnly ? await scrapeAutocomplete() : [];
  const redditResults = runAll || redditOnly ? await mineReddit() : [];
  const competitorResults =
    runAll || competitorsOnly ? await analyzeCompetitors() : [];

  console.log('\n[Scoring] Combining and ranking keywords...');
  const scored = scoreKeywords(autocompleteResults, redditResults, competitorResults);
  console.log(`[Scoring] Total unique keywords: ${scored.length}`);

  // Filter long-tail buyer intent keywords (4+ words)
  const longTailKeywords = scored.filter(
    k => k.keyword.split(/\s+/).length >= 4
  );

  // Build report
  const report = {
    generatedAt: new Date().toISOString(),
    summary: {
      totalKeywords: scored.length,
      longTailKeywords: longTailKeywords.length,
      bySource: {
        google_autocomplete: autocompleteResults.length,
        reddit: redditResults.length,
        competitor: competitorResults.length,
      },
      byCategory: Object.entries(
        scored.reduce(
          (acc, k) => {
            acc[k.category] = (acc[k.category] || 0) + 1;
            return acc;
          },
          {} as Record<string, number>
        )
      )
        .sort(([, a], [, b]) => b - a)
        .map(([category, count]) => ({ category, count })),
      byIntent: Object.entries(
        scored.reduce(
          (acc, k) => {
            acc[k.intent] = (acc[k.intent] || 0) + 1;
            return acc;
          },
          {} as Record<string, number>
        )
      ).map(([intent, count]) => ({ intent, count })),
    },
    topKeywords: scored.slice(0, 50),
    topLongTailKeywords: longTailKeywords.slice(0, 50),
    allKeywords: scored,
  };

  // Write outputs
  const timestamp = new Date().toISOString().split('T')[0];

  await writeFile(
    join(OUTPUT_DIR, `keywords_${timestamp}.json`),
    JSON.stringify(report, null, 2)
  );

  await writeFile(
    join(OUTPUT_DIR, `keywords_${timestamp}.csv`),
    generateCSV(scored)
  );

  // Print summary
  console.log('\n========================================');
  console.log('         KEYWORD REPORT SUMMARY');
  console.log('========================================\n');
  console.log(`Total keywords discovered: ${scored.length}`);
  console.log(`\nBy source:`);
  console.log(`  Google Autocomplete: ${autocompleteResults.length} raw keywords`);
  console.log(`  Reddit:              ${redditResults.length} keyword phrases`);
  console.log(`  Competitors:         ${competitorResults.length} content keywords`);

  console.log(`\nBy category:`);
  for (const { category, count } of report.summary.byCategory) {
    console.log(`  ${category.padEnd(22)} ${count}`);
  }

  console.log(`\nTop 25 keywords by score:`);
  console.log('-'.repeat(70));
  console.log(
    'Score'.padEnd(8) +
      'Keyword'.padEnd(40) +
      'Sources'.padEnd(15) +
      'Category'
  );
  console.log('-'.repeat(70));

  for (const kw of scored.slice(0, 25)) {
    console.log(
      kw.score.toFixed(1).padEnd(8) +
        kw.keyword.slice(0, 38).padEnd(40) +
        kw.sources.length.toString().padEnd(15) +
        kw.category
    );
  }

  console.log(`\nLong-tail keywords (4+ words): ${longTailKeywords.length}`);
  if (longTailKeywords.length > 0) {
    console.log(`\nTop 15 long-tail keywords (buyer intent):`);
    console.log('-'.repeat(75));
    console.log(
      'Score'.padEnd(8) +
        'Words'.padEnd(7) +
        'Keyword'.padEnd(50) +
        'Category'
    );
    console.log('-'.repeat(75));

    for (const kw of longTailKeywords.slice(0, 15)) {
      console.log(
        kw.score.toFixed(1).padEnd(8) +
          kw.keyword.split(/\s+/).length.toString().padEnd(7) +
          kw.keyword.slice(0, 48).padEnd(50) +
          kw.category
      );
    }
  }

  console.log(`\nReports saved to:`);
  console.log(`  JSON: ${join(OUTPUT_DIR, `keywords_${timestamp}.json`)}`);
  console.log(`  CSV:  ${join(OUTPUT_DIR, `keywords_${timestamp}.csv`)}`);
}

main().catch(console.error);
