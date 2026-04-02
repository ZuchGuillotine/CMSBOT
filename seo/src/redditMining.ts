import { SUBREDDITS, REDDIT_DELAY_MS } from './config.js';
import { delay, normalizeKeyword, extractNgrams } from './utils.js';

export interface RedditKeyword {
  keyword: string;
  frequency: number;
  subreddits: string[];
  sampleTitles: string[];
  source: 'reddit';
}

interface RedditPost {
  title: string;
  score: number;
  num_comments: number;
  subreddit: string;
}

async function fetchRedditPosts(
  subreddit: string,
  query: string,
  limit: number = 25
): Promise<RedditPost[]> {
  const params = new URLSearchParams({
    q: query,
    sort: 'relevance',
    limit: String(limit),
    t: 'year',
    restrict_sr: 'true',
  });

  try {
    const res = await fetch(
      `https://www.reddit.com/r/${subreddit}/search.json?${params}`,
      {
        headers: {
          'User-Agent': 'SEOKeywordResearch/1.0 (health-tracking-app)',
        },
      }
    );

    if (!res.ok) {
      console.warn(`  Reddit search failed for r/${subreddit} "${query}": ${res.status}`);
      return [];
    }

    const data = await res.json();
    return (data?.data?.children || []).map((child: any) => ({
      title: child.data.title,
      score: child.data.score,
      num_comments: child.data.num_comments,
      subreddit: child.data.subreddit,
    }));
  } catch (err) {
    console.warn(`  Reddit error for r/${subreddit}: ${(err as Error).message}`);
    return [];
  }
}

async function fetchTopPosts(subreddit: string, limit: number = 50): Promise<RedditPost[]> {
  try {
    const res = await fetch(
      `https://www.reddit.com/r/${subreddit}/top.json?t=month&limit=${limit}`,
      {
        headers: {
          'User-Agent': 'SEOKeywordResearch/1.0 (health-tracking-app)',
        },
      }
    );

    if (!res.ok) return [];

    const data = await res.json();
    return (data?.data?.children || []).map((child: any) => ({
      title: child.data.title,
      score: child.data.score,
      num_comments: child.data.num_comments,
      subreddit: child.data.subreddit,
    }));
  } catch {
    return [];
  }
}

export async function mineReddit(
  subreddits: string[] = SUBREDDITS
): Promise<RedditKeyword[]> {
  const allPosts: RedditPost[] = [];

  const searchTerms = [
    'tracking app',
    'best app',
    'how to track',
    'recommend',
    'blood work',
    'lab results',
    'supplement stack',
    'what supplements',
    'biomarker',
    'health data',
  ];

  console.log(`\n[Reddit] Mining ${subreddits.length} subreddits...`);

  // Fetch top posts from each subreddit
  for (const sub of subreddits) {
    await delay(REDDIT_DELAY_MS);
    const topPosts = await fetchTopPosts(sub);
    allPosts.push(...topPosts);
    console.log(`  r/${sub} top posts: ${topPosts.length}`);
  }

  // Search each subreddit for relevant terms
  for (const sub of subreddits) {
    for (const term of searchTerms) {
      await delay(REDDIT_DELAY_MS);
      const posts = await fetchRedditPosts(sub, term);
      allPosts.push(...posts);
    }
    console.log(`  r/${sub} search complete`);
  }

  console.log(`[Reddit] Total posts collected: ${allPosts.length}`);

  // Extract keywords from post titles using n-grams
  const ngramData = new Map<
    string,
    { count: number; subreddits: Set<string>; titles: string[] }
  >();

  for (const post of allPosts) {
    const bigrams = extractNgrams(post.title, 2);
    const trigrams = extractNgrams(post.title, 3);

    for (const gram of [...bigrams, ...trigrams]) {
      const entry = ngramData.get(gram) || {
        count: 0,
        subreddits: new Set(),
        titles: [],
      };
      entry.count++;
      entry.subreddits.add(post.subreddit);
      if (entry.titles.length < 3) entry.titles.push(post.title);
      ngramData.set(gram, entry);
    }
  }

  // Extract question patterns as long-tail keyword candidates
  const questionPosts = allPosts.filter(p =>
    /^(how|what|why|when|where|which|best|recommend|should|can|does|is there)/i.test(
      p.title
    )
  );

  for (const post of questionPosts) {
    const normalized = normalizeKeyword(post.title)
      .replace(/[?!.,]/g, '')
      .slice(0, 80);

    if (normalized.split(' ').length >= 3 && normalized.split(' ').length <= 10) {
      const entry = ngramData.get(normalized) || {
        count: 0,
        subreddits: new Set(),
        titles: [],
      };
      entry.count++;
      entry.subreddits.add(post.subreddit);
      if (entry.titles.length < 3) entry.titles.push(post.title);
      ngramData.set(normalized, entry);
    }
  }

  // Filter to keywords that appear enough to be meaningful
  const results: RedditKeyword[] = [];

  for (const [keyword, data] of ngramData) {
    // Long-tail questions (4+ words) pass with frequency 1 — these are rare by
    // definition but high-value for a new entrant targeting buyer-intent queries
    const wordCount = keyword.split(' ').length;
    const isLongTail = wordCount >= 4;
    if (data.count >= 2 || (isLongTail && data.count >= 1)) {
      results.push({
        keyword,
        frequency: data.count,
        subreddits: [...data.subreddits],
        sampleTitles: data.titles,
        source: 'reddit',
      });
    }
  }

  results.sort((a, b) => b.frequency - a.frequency);

  console.log(`[Reddit] Extracted ${results.length} keyword phrases`);
  return results;
}
