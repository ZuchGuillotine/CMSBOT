import { COMPETITORS, REQUEST_DELAY_MS } from './config.js';
import { delay, normalizeKeyword } from './utils.js';

export interface CompetitorKeyword {
  keyword: string;
  competitor: string;
  url: string;
  contentType: string;
  source: 'competitor';
}

function extractKeywordsFromUrl(url: string): string[] {
  try {
    const pathname = new URL(url).pathname;
    const segments = pathname
      .split('/')
      .filter(s => s.length > 0)
      .filter(s => !s.match(/^\d+$/) && s.length > 2);

    const keywords: string[] = [];

    for (const segment of segments) {
      const phrase = segment
        .replace(/[-_]/g, ' ')
        .replace(/\.(html|php|aspx?)$/i, '')
        .toLowerCase()
        .trim();

      if (phrase.length > 3 && phrase.split(' ').length <= 8) {
        keywords.push(phrase);
      }
    }

    return keywords;
  } catch {
    return [];
  }
}

function classifyContentType(url: string): string {
  const path = url.toLowerCase();
  if (path.includes('/blog') || path.includes('/article') || path.includes('/post'))
    return 'blog';
  if (path.includes('/guide') || path.includes('/learn') || path.includes('/resource'))
    return 'guide';
  if (path.includes('/product') || path.includes('/feature')) return 'product';
  if (path.includes('/faq') || path.includes('/help') || path.includes('/support'))
    return 'faq';
  if (path.includes('/research') || path.includes('/study') || path.includes('/science'))
    return 'research';
  return 'page';
}

async function fetchSitemap(domain: string, sitemapPath: string): Promise<string[]> {
  const urls: string[] = [];
  const sitemapUrl = `https://${domain}${sitemapPath}`;

  try {
    const res = await fetch(sitemapUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; SEOResearchBot/1.0)',
      },
    });

    if (!res.ok) {
      console.warn(`  Sitemap fetch failed for ${domain}: ${res.status}`);
      return [];
    }

    const xml = await res.text();

    // Check if this is a sitemap index (contains references to other sitemaps)
    const sitemapRefs: string[] = [];
    for (const match of xml.matchAll(/<sitemap>\s*<loc>(.*?)<\/loc>/gs)) {
      sitemapRefs.push(match[1]);
    }

    if (sitemapRefs.length > 0) {
      // Fetch child sitemaps (limit to first 5 to stay polite)
      for (const childUrl of sitemapRefs.slice(0, 5)) {
        await delay(REQUEST_DELAY_MS);
        try {
          const childRes = await fetch(childUrl, {
            headers: { 'User-Agent': 'Mozilla/5.0 (compatible; SEOResearchBot/1.0)' },
          });
          if (childRes.ok) {
            const childXml = await childRes.text();
            for (const m of childXml.matchAll(/<loc>(.*?)<\/loc>/g)) {
              urls.push(m[1]);
            }
          }
        } catch {
          // Skip failed child sitemaps
        }
      }
    } else {
      // Regular sitemap — extract URLs directly
      for (const match of xml.matchAll(/<loc>(.*?)<\/loc>/g)) {
        urls.push(match[1]);
      }
    }
  } catch (err) {
    console.warn(`  Sitemap error for ${domain}: ${(err as Error).message}`);
  }

  return urls;
}

export async function analyzeCompetitors(
  competitors = COMPETITORS
): Promise<CompetitorKeyword[]> {
  const results: CompetitorKeyword[] = [];
  const seen = new Set<string>();

  console.log(`\n[Competitors] Analyzing ${competitors.length} competitor sitemaps...`);

  for (const competitor of competitors) {
    await delay(REQUEST_DELAY_MS);
    const urls = await fetchSitemap(competitor.domain, competitor.sitemapPath);
    console.log(`  ${competitor.name}: ${urls.length} URLs found`);

    for (const url of urls) {
      const keywords = extractKeywordsFromUrl(url);
      const contentType = classifyContentType(url);

      for (const kw of keywords) {
        const normalized = normalizeKeyword(kw);
        const key = `${normalized}:${competitor.name}`;
        if (!seen.has(key)) {
          seen.add(key);
          results.push({
            keyword: normalized,
            competitor: competitor.name,
            url,
            contentType,
            source: 'competitor',
          });
        }
      }
    }
  }

  console.log(`[Competitors] Total keywords from competitor content: ${results.length}`);
  return results;
}
