import { BLOG_API_URL, BLOG_PATH_PREFIX, GSC_SITE_URL } from './config.js';
import type { GSCKeywordData, GSCPageData } from './gsc.js';
import type { GA4PageData } from './ga4.js';
import type { ScoredKeyword } from './scorer.js';
import { normalizeKeyword } from './utils.js';

export interface ContentScorecard {
  slug: string;
  title: string;
  publishedAt: string;
  url: string;
  targetKeywords: string[];
  rankingKeywords: Array<{
    query: string;
    clicks: number;
    impressions: number;
    ctr: number;
    position: number;
  }>;
  traffic: GA4PageData | null;
  performanceScore: number;
  opportunities: string[];
}

interface BlogPost {
  slug: string;
  title: string;
  publishedAt: string;
  tags?: string[];
  metaDescription?: string;
}

export async function fetchPublishedPosts(): Promise<BlogPost[]> {
  if (!BLOG_API_URL) {
    console.warn('[Content] BLOG_API_URL is not configured. Skipping blog post fetch.');
    return [];
  }

  console.log(`[Content] Fetching published blog posts from ${BLOG_API_URL}...`);

  try {
    const res = await fetch(BLOG_API_URL);
    if (!res.ok) {
      console.warn(`[Content] Blog API returned ${res.status}, using empty list`);
      return [];
    }
    const data = await res.json();
    // Handle both array responses and { posts: [...] } wrappers
    const posts = Array.isArray(data) ? data : data.posts || [];
    return posts.map((p: any) => ({
      slug: p.slug,
      title: p.title,
      publishedAt: p.publishedAt || p.published_at || '',
      tags: p.tags || [],
      metaDescription: p.metaDescription || p.meta_description || p.excerpt || '',
    }));
  } catch (err) {
    console.warn(`[Content] Failed to fetch blog posts: ${(err as Error).message}`);
    return [];
  }
}

export function buildContentScorecards(
  posts: BlogPost[],
  gscKeywords: GSCKeywordData[],
  gscPages: GSCPageData[],
  ga4Pages: GA4PageData[]
): ContentScorecard[] {
  const scorecards: ContentScorecard[] = [];

  // Build lookup maps
  const ga4Map = new Map<string, GA4PageData>();
  for (const page of ga4Pages) {
    ga4Map.set(page.path, page);
  }

  const gscPageMap = new Map<string, GSCPageData>();
  for (const page of gscPages) {
    // Extract path from full URL
    try {
      const path = new URL(page.page).pathname;
      gscPageMap.set(path, page);
    } catch {
      gscPageMap.set(page.page, page);
    }
  }

  // Build keyword → GSC data map for quick lookup
  const gscKeywordMap = new Map<string, GSCKeywordData>();
  for (const kw of gscKeywords) {
    gscKeywordMap.set(kw.query, kw);
  }

  for (const post of posts) {
    const blogPath = `${BLOG_PATH_PREFIX.replace(/\/$/, '')}/${post.slug}`;
    const fullUrl = GSC_SITE_URL ? `${GSC_SITE_URL}${blogPath}` : blogPath;

    // Find GSC data for this page
    const gscPage = gscPageMap.get(blogPath);
    const rankingKeywords: ContentScorecard['rankingKeywords'] = [];

    if (gscPage) {
      for (const query of gscPage.queries) {
        const kwData = gscKeywordMap.get(query);
        if (kwData) {
          rankingKeywords.push({
            query: kwData.query,
            clicks: kwData.clicks,
            impressions: kwData.impressions,
            ctr: kwData.ctr,
            position: kwData.position,
          });
        }
      }
    }

    // Also check gscKeywords for any that list this page
    for (const kw of gscKeywords) {
      if (
        kw.pages.some(p => p.includes(post.slug)) &&
        !rankingKeywords.find(rk => rk.query === kw.query)
      ) {
        rankingKeywords.push({
          query: kw.query,
          clicks: kw.clicks,
          impressions: kw.impressions,
          ctr: kw.ctr,
          position: kw.position,
        });
      }
    }

    rankingKeywords.sort((a, b) => b.impressions - a.impressions);

    // GA4 traffic data
    const traffic = ga4Map.get(blogPath) || null;

    // Identify opportunities
    const opportunities: string[] = [];
    const topKeyword = rankingKeywords[0];
    const daysSincePublish = post.publishedAt
      ? (Date.now() - new Date(post.publishedAt).getTime()) / (1000 * 60 * 60 * 24)
      : 0;

    if (rankingKeywords.length === 0 && daysSincePublish > 30) {
      opportunities.push('No ranking keywords after 30+ days — review targeting');
    }
    if (topKeyword && topKeyword.position >= 5 && topKeyword.position <= 15) {
      opportunities.push(
        `Quick win: "${topKeyword.query}" at position ${topKeyword.position.toFixed(1)} — optimize to reach top 5`
      );
    }
    if (topKeyword && topKeyword.position <= 5 && topKeyword.ctr < 0.05) {
      opportunities.push(
        `Low CTR (${(topKeyword.ctr * 100).toFixed(1)}%) despite position ${topKeyword.position.toFixed(1)} — rewrite title/meta`
      );
    }
    if (traffic && traffic.bounceRate > 0.7) {
      opportunities.push(
        `High bounce rate (${(traffic.bounceRate * 100).toFixed(0)}%) — improve content engagement`
      );
    }

    // Composite performance score
    let performanceScore = 0;
    const totalImpressions = rankingKeywords.reduce((s, k) => s + k.impressions, 0);
    const totalClicks = rankingKeywords.reduce((s, k) => s + k.clicks, 0);
    performanceScore += Math.min(20, totalImpressions / 100);
    performanceScore += Math.min(20, totalClicks * 2);
    if (traffic) {
      performanceScore += Math.min(10, traffic.pageviews / 50);
      performanceScore += Math.min(10, traffic.organicTraffic / 25);
      performanceScore += traffic.bounceRate < 0.5 ? 5 : 0;
    }
    if (rankingKeywords.some(k => k.position <= 3)) performanceScore += 10;
    else if (rankingKeywords.some(k => k.position <= 10)) performanceScore += 5;

    scorecards.push({
      slug: post.slug,
      title: post.title,
      publishedAt: post.publishedAt,
      url: fullUrl,
      targetKeywords: post.tags || [],
      rankingKeywords,
      traffic,
      performanceScore,
      opportunities,
    });
  }

  scorecards.sort((a, b) => b.performanceScore - a.performanceScore);
  console.log(`[Content] Built scorecards for ${scorecards.length} posts`);
  return scorecards;
}

export function findContentGaps(
  seoKeywords: ScoredKeyword[],
  gscKeywords: GSCKeywordData[],
  scorecards: ContentScorecard[]
): ScoredKeyword[] {
  // Keywords from SEO research that we have no content ranking for
  const rankedQueries = new Set<string>();

  for (const gsc of gscKeywords) {
    rankedQueries.add(normalizeKeyword(gsc.query));
  }

  for (const card of scorecards) {
    for (const kw of card.targetKeywords) {
      rankedQueries.add(normalizeKeyword(kw));
    }
  }

  return seoKeywords.filter(
    kw => !rankedQueries.has(normalizeKeyword(kw.keyword)) && kw.score >= 10
  );
}
