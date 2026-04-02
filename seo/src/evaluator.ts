import { EXPECTED_CTR_BY_POSITION } from './config.js';
import type { GSCKeywordData } from './gsc.js';
import type { ContentScorecard } from './contentPerformance.js';
import type { ScoredKeyword } from './scorer.js';
import { normalizeKeyword } from './utils.js';

// Recommendation buckets
export interface QuickWin {
  keyword: string;
  position: number;
  impressions: number;
  page: string;
  action: string;
}

export interface CTROpportunity {
  keyword: string;
  position: number;
  currentCTR: number;
  expectedCTR: number;
  impressions: number;
  page: string;
  action: string;
}

export interface ContentGap {
  keyword: string;
  seoScore: number;
  category: string;
  intent: string;
  sources: string[];
  reason: string;
}

export interface Underperformer {
  slug: string;
  title: string;
  daysSincePublish: number;
  impressions: number;
  clicks: number;
  action: string;
}

export interface Decliner {
  keyword: string;
  currentPosition: number;
  previousPosition: number;
  positionDelta: number;
  page: string;
  action: string;
}

export interface ConversionStar {
  slug: string;
  title: string;
  conversions: number;
  topKeywords: string[];
  action: string;
}

export interface Cannibalization {
  keyword: string;
  pages: string[];
  action: string;
}

export interface TopicBrief {
  cluster: string;
  title_seed: string;
  prompt: string;
  target_keywords: string[];
  priority: number;
  source: 'evaluator';
  reason: string;
}

export interface EvaluationReport {
  generatedAt: string;
  period: { days: number; compareDays: number };
  summary: {
    totalKeywordsTracked: number;
    totalContentPieces: number;
    avgPosition: number;
    totalImpressions: number;
    totalClicks: number;
    overallCTR: number;
  };
  quickWins: QuickWin[];
  ctrOpportunities: CTROpportunity[];
  contentGaps: ContentGap[];
  underperformers: Underperformer[];
  decliners: Decliner[];
  conversionStars: ConversionStar[];
  cannibalization: Cannibalization[];
  topicBriefs: TopicBrief[];
  contentScorecards: ContentScorecard[];
}

export function evaluate(
  gscKeywords: GSCKeywordData[],
  scorecards: ContentScorecard[],
  seoGaps: ScoredKeyword[],
  movers: Array<{
    query: string;
    currentPosition: number;
    previousPosition: number;
    positionDelta: number;
    currentImpressions: number;
  }>,
  days: number,
  compareDays: number
): EvaluationReport {
  console.log('\n[Evaluator] Analyzing performance data...');

  // Summary stats
  const totalImpressions = gscKeywords.reduce((s, k) => s + k.impressions, 0);
  const totalClicks = gscKeywords.reduce((s, k) => s + k.clicks, 0);
  const avgPosition =
    gscKeywords.length > 0
      ? gscKeywords.reduce((s, k) => s + k.position, 0) / gscKeywords.length
      : 0;

  // Quick Wins: keywords at positions 5-15 with decent impressions
  const quickWins: QuickWin[] = gscKeywords
    .filter(k => k.position >= 5 && k.position <= 15 && k.impressions >= 100)
    .sort((a, b) => b.impressions - a.impressions)
    .slice(0, 20)
    .map(k => ({
      keyword: k.query,
      position: k.position,
      impressions: k.impressions,
      page: k.pages[0] || '',
      action: `Optimize content for "${k.query}" — currently position ${k.position.toFixed(1)} with ${k.impressions} impressions. Add depth, internal links, and improve on-page SEO.`,
    }));

  // CTR Opportunities: good ranking but CTR below expected
  const ctrOpportunities: CTROpportunity[] = gscKeywords
    .filter(k => {
      const pos = Math.round(k.position);
      const expected = EXPECTED_CTR_BY_POSITION[pos] || 0.01;
      return k.position <= 10 && k.ctr < expected * 0.7 && k.impressions >= 50;
    })
    .sort((a, b) => b.impressions - a.impressions)
    .slice(0, 15)
    .map(k => {
      const pos = Math.round(k.position);
      const expected = EXPECTED_CTR_BY_POSITION[pos] || 0.01;
      return {
        keyword: k.query,
        position: k.position,
        currentCTR: k.ctr,
        expectedCTR: expected,
        impressions: k.impressions,
        page: k.pages[0] || '',
        action: `CTR for "${k.query}" is ${(k.ctr * 100).toFixed(1)}% vs expected ${(expected * 100).toFixed(0)}% at position ${pos}. Rewrite title tag and meta description to improve click-through.`,
      };
    });

  // Content Gaps: high-scoring SEO keywords with no published content
  const contentGaps: ContentGap[] = seoGaps
    .sort((a, b) => b.score - a.score)
    .slice(0, 30)
    .map(k => ({
      keyword: k.keyword,
      seoScore: k.score,
      category: k.category,
      intent: k.intent,
      sources: k.sources,
      reason: `SEO score ${k.score.toFixed(1)} from ${k.sources.join(' + ')} — no published content targeting this keyword.`,
    }));

  // Underperformers: published > 30 days with < 50 impressions
  const underperformers: Underperformer[] = scorecards
    .filter(c => {
      const daysSincePublish = c.publishedAt
        ? (Date.now() - new Date(c.publishedAt).getTime()) / (1000 * 60 * 60 * 24)
        : 0;
      const totalImpressions = c.rankingKeywords.reduce(
        (s, k) => s + k.impressions,
        0
      );
      return daysSincePublish > 30 && totalImpressions < 50;
    })
    .map(c => {
      const daysSincePublish = c.publishedAt
        ? (Date.now() - new Date(c.publishedAt).getTime()) / (1000 * 60 * 60 * 24)
        : 0;
      const totalImp = c.rankingKeywords.reduce((s, k) => s + k.impressions, 0);
      const totalClk = c.rankingKeywords.reduce((s, k) => s + k.clicks, 0);
      return {
        slug: c.slug,
        title: c.title,
        daysSincePublish: Math.round(daysSincePublish),
        impressions: totalImp,
        clicks: totalClk,
        action: `"${c.title}" published ${Math.round(daysSincePublish)} days ago with only ${totalImp} impressions. Review keyword targeting, add schema markup, or consider rewriting.`,
      };
    });

  // Decliners: keywords losing position
  const decliners: Decliner[] = movers
    .filter(m => m.positionDelta < -3 && m.currentImpressions >= 50)
    .sort((a, b) => a.positionDelta - b.positionDelta)
    .slice(0, 15)
    .map(m => {
      const page =
        gscKeywords.find(k => k.query === m.query)?.pages[0] || '';
      return {
        keyword: m.query,
        currentPosition: m.currentPosition,
        previousPosition: m.previousPosition,
        positionDelta: m.positionDelta,
        page,
        action: `"${m.query}" dropped from position ${m.previousPosition.toFixed(1)} → ${m.currentPosition.toFixed(1)}. Refresh content with updated info, improve internal linking.`,
      };
    });

  // Conversion Stars: content with above-average conversion rate
  const avgConversions =
    scorecards.length > 0
      ? scorecards.reduce((s, c) => s + (c.traffic?.conversions || 0), 0) /
        scorecards.length
      : 0;

  const conversionStars: ConversionStar[] = scorecards
    .filter(c => (c.traffic?.conversions || 0) > avgConversions && avgConversions > 0)
    .sort(
      (a, b) =>
        (b.traffic?.conversions || 0) - (a.traffic?.conversions || 0)
    )
    .slice(0, 10)
    .map(c => ({
      slug: c.slug,
      title: c.title,
      conversions: c.traffic?.conversions || 0,
      topKeywords: c.rankingKeywords.slice(0, 5).map(k => k.query),
      action: `"${c.title}" drives ${c.traffic?.conversions || 0} conversions. Create related content targeting similar keywords: ${c.rankingKeywords
        .slice(0, 3)
        .map(k => k.query)
        .join(', ')}.`,
    }));

  // Cannibalization: multiple pages ranking for the same keyword
  const cannibalization: Cannibalization[] = [];
  const queryPages = new Map<string, string[]>();
  for (const kw of gscKeywords) {
    if (kw.pages.length > 1) {
      queryPages.set(kw.query, kw.pages);
    }
  }
  for (const [query, pages] of queryPages) {
    if (pages.length > 1) {
      cannibalization.push({
        keyword: query,
        pages,
        action: `"${query}" ranks for ${pages.length} pages. Consolidate content or add canonical tags to focus authority.`,
      });
    }
  }

  // Generate CMSBOT topic briefs from content gaps
  const topicBriefs: TopicBrief[] = contentGaps.slice(0, 10).map(gap => {
    const titleSeed = generateTitleFromKeyword(gap.keyword, gap.intent);
    return {
      cluster: gap.category,
      title_seed: titleSeed,
      prompt: buildContentPrompt(gap.keyword, gap.category, gap.intent),
      target_keywords: [
        gap.keyword,
        ...findRelatedKeywords(gap.keyword, seoGaps).slice(0, 4),
      ],
      priority: gap.seoScore >= 20 ? 1 : gap.seoScore >= 15 ? 2 : 3,
      source: 'evaluator' as const,
      reason: gap.reason,
    };
  });

  const report: EvaluationReport = {
    generatedAt: new Date().toISOString(),
    period: { days, compareDays },
    summary: {
      totalKeywordsTracked: gscKeywords.length,
      totalContentPieces: scorecards.length,
      avgPosition,
      totalImpressions,
      totalClicks,
      overallCTR: totalImpressions > 0 ? totalClicks / totalImpressions : 0,
    },
    quickWins,
    ctrOpportunities,
    contentGaps,
    underperformers,
    decliners,
    conversionStars,
    cannibalization,
    topicBriefs,
    contentScorecards: scorecards,
  };

  // Print summary
  console.log(`\n[Evaluator] Results:`);
  console.log(`  Quick wins:        ${quickWins.length}`);
  console.log(`  CTR opportunities: ${ctrOpportunities.length}`);
  console.log(`  Content gaps:      ${contentGaps.length}`);
  console.log(`  Underperformers:   ${underperformers.length}`);
  console.log(`  Declining:         ${decliners.length}`);
  console.log(`  Conversion stars:  ${conversionStars.length}`);
  console.log(`  Cannibalization:   ${cannibalization.length}`);
  console.log(`  Topic briefs:      ${topicBriefs.length}`);

  return report;
}

function generateTitleFromKeyword(keyword: string, intent: string): string {
  const kw = keyword.toLowerCase();

  if (intent === 'informational') {
    if (kw.startsWith('how')) return capitalizeFirst(kw);
    if (kw.startsWith('what')) return capitalizeFirst(kw);
    return `The Complete Guide to ${capitalizeFirst(kw)}`;
  }
  if (intent === 'commercial') {
    return `Best ${capitalizeFirst(kw)} in ${new Date().getFullYear()}`;
  }
  return `${capitalizeFirst(kw)}: What You Need to Know`;
}

function buildContentPrompt(
  keyword: string,
  category: string,
  intent: string
): string {
  const base = `Write a comprehensive, evidence-based article targeting the keyword "${keyword}".`;
  const audience =
    'The target audience is health-conscious individuals who track their supplements, lab results, and biomarkers.';

  if (category === 'supplements') {
    return `${base} ${audience} Include dosage guidance, timing recommendations, and how tracking intake helps optimize results. Reference relevant research.`;
  }
  if (category === 'lab-results') {
    return `${base} ${audience} Explain what the biomarkers mean, optimal ranges vs standard ranges, and how to track changes over time.`;
  }
  if (intent === 'commercial') {
    return `${base} ${audience} Compare options objectively, highlight key features for the health-tracking use case, and help readers make an informed decision.`;
  }
  return `${base} ${audience} Be thorough but accessible. Include actionable takeaways.`;
}

function findRelatedKeywords(
  keyword: string,
  allGaps: ScoredKeyword[]
): string[] {
  const words = new Set(normalizeKeyword(keyword).split(/\s+/));
  return allGaps
    .filter(g => {
      const gWords = normalizeKeyword(g.keyword).split(/\s+/);
      const overlap = gWords.filter(w => words.has(w)).length;
      return overlap > 0 && g.keyword !== keyword;
    })
    .sort((a, b) => b.score - a.score)
    .map(g => g.keyword);
}

function capitalizeFirst(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
