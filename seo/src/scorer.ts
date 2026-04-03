import { RELEVANCE_TERMS, BUYER_INTENT_SIGNALS } from './config.js';
import type { AutocompleteKeyword } from './googleAutocomplete.js';
import type { RedditKeyword } from './redditMining.js';
import type { CompetitorKeyword } from './competitorGap.js';

export interface GSCPerformanceData {
  query: string;
  impressions: number;
  clicks: number;
  position: number;
}

export interface ScoredKeyword {
  keyword: string;
  score: number;
  sources: string[];
  category: string;
  intent: string;
  details: {
    autocompletePosition?: number;
    redditFrequency?: number;
    redditSubreddits?: string[];
    competitorNames?: string[];
    competitorContentTypes?: string[];
    gscImpressions?: number;
    gscPosition?: number;
    gscClicks?: number;
  };
}

function categorize(keyword: string): string {
  const kw = keyword.toLowerCase();
  if (/supplement|vitamin|mineral|stack|nootropic|peptide|dose|dosage/.test(kw))
    return 'supplements';
  if (/lab|blood|biomarker|test|panel|result/.test(kw)) return 'lab-results';
  if (/track|log|monitor|app|dashboard/.test(kw)) return 'tracking';
  if (/ai|insight|analy|recommend|personali/.test(kw)) return 'ai-health';
  if (/health|optimi|biohack|longevity|wellness/.test(kw)) return 'health-optimization';
  if (/diet|nutrition|food|calori|macro/.test(kw)) return 'nutrition';
  if (/sleep|recovery|stress|mental/.test(kw)) return 'wellness';
  return 'general';
}

function classifyIntent(keyword: string): string {
  const kw = keyword.toLowerCase();
  if (/^(how|what|why|when|where|which|does|is|can|should|do)\b/.test(kw))
    return 'informational';
  if (/\b(best|top|review|vs|compare|alternative)\b/.test(kw)) return 'commercial';
  if (/\b(buy|price|discount|free|download|sign up|app)\b/.test(kw))
    return 'transactional';
  return 'informational';
}

function relevanceScore(keyword: string): number {
  const words = keyword.toLowerCase().split(/\s+/);
  let score = 0;
  for (const word of words) {
    if (RELEVANCE_TERMS.includes(word)) score += 2;
  }
  if (words.length >= 3) score += 1;
  if (words.length >= 4) score += 1;
  return score;
}

function longTailBuyerIntentScore(keyword: string): number {
  const kw = keyword.toLowerCase();
  const wordCount = kw.split(/\s+/).length;
  let score = 0;

  // Long-tail length bonus — this is the big one. 4-6 word phrases are the sweet spot
  // for a new entrant: specific enough to rank, intent-rich enough to convert.
  if (wordCount >= 4) score += 4;
  if (wordCount >= 5) score += 4;
  if (wordCount >= 6) score += 3;
  if (wordCount >= 7) score += 1; // diminishing — too long gets fragmented

  // Buyer intent signal matching — check if the keyword contains phrases
  // that indicate someone is ready to take action
  let intentMatches = 0;
  for (const signal of BUYER_INTENT_SIGNALS) {
    if (kw.includes(signal)) {
      intentMatches++;
    }
  }
  score += Math.min(8, intentMatches * 3);

  // Compound bonus: long-tail AND buyer intent together is the prime target.
  // "magnesium supplement" = meh. "best form of magnesium for sleep" = gold.
  if (wordCount >= 4 && intentMatches >= 1) {
    score += 5;
  }

  return score;
}

export function scoreKeywords(
  autocomplete: AutocompleteKeyword[],
  reddit: RedditKeyword[],
  competitor: CompetitorKeyword[],
  gscData?: GSCPerformanceData[]
): ScoredKeyword[] {
  const keywordMap = new Map<string, ScoredKeyword>();

  function getOrCreate(keyword: string): ScoredKeyword {
    let entry = keywordMap.get(keyword);
    if (!entry) {
      entry = {
        keyword,
        score: 0,
        sources: [],
        category: categorize(keyword),
        intent: classifyIntent(keyword),
        details: {},
      };
      keywordMap.set(keyword, entry);
    }
    return entry;
  }

  // Process autocomplete — earlier position implies higher search volume
  for (const ac of autocomplete) {
    const entry = getOrCreate(ac.keyword);
    if (!entry.sources.includes('google_autocomplete')) {
      entry.sources.push('google_autocomplete');
    }
    const posScore = Math.max(0, 10 - ac.position * 1.5);
    entry.details.autocompletePosition = Math.min(
      ac.position,
      entry.details.autocompletePosition ?? Infinity
    );
    entry.score += posScore;
  }

  // Process Reddit — frequency and cross-subreddit presence
  for (const rd of reddit) {
    const entry = getOrCreate(rd.keyword);
    if (!entry.sources.includes('reddit')) {
      entry.sources.push('reddit');
    }
    entry.details.redditFrequency = (entry.details.redditFrequency || 0) + rd.frequency;
    entry.details.redditSubreddits = [
      ...new Set([...(entry.details.redditSubreddits || []), ...rd.subreddits]),
    ];
    entry.score += Math.min(10, rd.frequency * 2);
    if (rd.subreddits.length > 1) entry.score += rd.subreddits.length;
  }

  // Process competitors — topics they create content around
  for (const comp of competitor) {
    const entry = getOrCreate(comp.keyword);
    if (!entry.sources.includes('competitor')) {
      entry.sources.push('competitor');
    }
    entry.details.competitorNames = [
      ...new Set([...(entry.details.competitorNames || []), comp.competitor]),
    ];
    entry.details.competitorContentTypes = [
      ...new Set([
        ...(entry.details.competitorContentTypes || []),
        comp.contentType,
      ]),
    ];
    entry.score += 3;
    if ((entry.details.competitorNames?.length || 0) > 1) {
      entry.score += 2;
    }
  }

  // Process GSC performance data when available
  if (gscData) {
    for (const gsc of gscData) {
      const normalized = gsc.query.toLowerCase().trim();
      const entry = keywordMap.get(normalized);
      if (entry) {
        if (!entry.sources.includes('gsc')) {
          entry.sources.push('gsc');
        }
        entry.details.gscImpressions = gsc.impressions;
        entry.details.gscPosition = gsc.position;
        entry.details.gscClicks = gsc.clicks;

        // Validated demand from real search data
        entry.score += Math.min(10, gsc.impressions / 200);

        // Optimization opportunity: ranking 5-15 = boost priority
        if (gsc.position >= 5 && gsc.position <= 15) {
          entry.score += 5;
        }
        // Already winning: ranking 1-3 = reduce priority (less urgent)
        if (gsc.position <= 3) {
          entry.score -= 3;
        }
      }
    }
  }

  // Apply relevance, long-tail buyer intent, and multi-source bonuses
  for (const entry of keywordMap.values()) {
    entry.score += relevanceScore(entry.keyword);
    entry.score += longTailBuyerIntentScore(entry.keyword);
    if (entry.sources.length >= 2) entry.score += 5;
    if (entry.sources.length >= 3) entry.score += 5;
  }

  return [...keywordMap.values()].sort((a, b) => b.score - a.score);
}
