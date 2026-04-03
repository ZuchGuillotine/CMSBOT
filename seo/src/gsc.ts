import { google } from 'googleapis';
import { GSC_SITE_URL, GOOGLE_SERVICE_ACCOUNT_KEY_PATH } from './config.js';

export interface GSCKeywordData {
  query: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
  pages: string[];
}

export interface GSCPageData {
  page: string;
  clicks: number;
  impressions: number;
  ctr: number;
  position: number;
  queries: string[];
}

interface DateRange {
  startDate: string; // YYYY-MM-DD
  endDate: string;
}

function buildDateRange(days: number): DateRange {
  const end = new Date();
  end.setDate(end.getDate() - 1); // GSC data lags ~2 days
  const start = new Date(end);
  start.setDate(start.getDate() - days);
  return {
    startDate: start.toISOString().split('T')[0],
    endDate: end.toISOString().split('T')[0],
  };
}

async function getClient() {
  if (!GOOGLE_SERVICE_ACCOUNT_KEY_PATH) {
    throw new Error(
      'GOOGLE_SERVICE_ACCOUNT_KEY_PATH not set. Provide the path to your service account JSON key.'
    );
  }
  if (!GSC_SITE_URL) {
    throw new Error(
      'GSC_SITE_URL not set. Populate it in CMSBOT_PROJECT_FILE or export GSC_SITE_URL.'
    );
  }

  const auth = new google.auth.GoogleAuth({
    keyFile: GOOGLE_SERVICE_ACCOUNT_KEY_PATH,
    scopes: ['https://www.googleapis.com/auth/webmasters.readonly'],
  });

  return google.searchconsole({ version: 'v1', auth });
}

export async function getKeywordRankings(
  days: number = 28
): Promise<GSCKeywordData[]> {
  const client = await getClient();
  const { startDate, endDate } = buildDateRange(days);

  console.log(`[GSC] Fetching keyword rankings (${startDate} → ${endDate})...`);

  const keywordMap = new Map<string, GSCKeywordData>();

  // Query-level data
  const queryRes = await client.searchanalytics.query({
    siteUrl: GSC_SITE_URL,
    requestBody: {
      startDate,
      endDate,
      dimensions: ['query'],
      rowLimit: 5000,
    },
  });

  for (const row of queryRes.data.rows || []) {
    const query = row.keys![0];
    keywordMap.set(query, {
      query,
      clicks: row.clicks || 0,
      impressions: row.impressions || 0,
      ctr: row.ctr || 0,
      position: row.position || 0,
      pages: [],
    });
  }

  // Query + Page data to map keywords to pages
  const pageRes = await client.searchanalytics.query({
    siteUrl: GSC_SITE_URL,
    requestBody: {
      startDate,
      endDate,
      dimensions: ['query', 'page'],
      rowLimit: 10000,
    },
  });

  for (const row of pageRes.data.rows || []) {
    const query = row.keys![0];
    const page = row.keys![1];
    const entry = keywordMap.get(query);
    if (entry && !entry.pages.includes(page)) {
      entry.pages.push(page);
    }
  }

  const results = [...keywordMap.values()].sort(
    (a, b) => b.impressions - a.impressions
  );
  console.log(`[GSC] ${results.length} keywords retrieved`);
  return results;
}

export async function getPagePerformance(
  days: number = 28
): Promise<GSCPageData[]> {
  const client = await getClient();
  const { startDate, endDate } = buildDateRange(days);

  console.log(`[GSC] Fetching page performance (${startDate} → ${endDate})...`);

  const pageMap = new Map<string, GSCPageData>();

  // Page-level data
  const pageRes = await client.searchanalytics.query({
    siteUrl: GSC_SITE_URL,
    requestBody: {
      startDate,
      endDate,
      dimensions: ['page'],
      rowLimit: 5000,
    },
  });

  for (const row of pageRes.data.rows || []) {
    const page = row.keys![0];
    pageMap.set(page, {
      page,
      clicks: row.clicks || 0,
      impressions: row.impressions || 0,
      ctr: row.ctr || 0,
      position: row.position || 0,
      queries: [],
    });
  }

  // Page + Query to map pages to their keywords
  const queryRes = await client.searchanalytics.query({
    siteUrl: GSC_SITE_URL,
    requestBody: {
      startDate,
      endDate,
      dimensions: ['page', 'query'],
      rowLimit: 10000,
    },
  });

  for (const row of queryRes.data.rows || []) {
    const page = row.keys![0];
    const query = row.keys![1];
    const entry = pageMap.get(page);
    if (entry && !entry.queries.includes(query)) {
      entry.queries.push(query);
    }
  }

  const results = [...pageMap.values()].sort(
    (a, b) => b.impressions - a.impressions
  );
  console.log(`[GSC] ${results.length} pages retrieved`);
  return results;
}

export async function getTopMovers(
  days: number = 28,
  compareDays: number = 28
): Promise<
  Array<{
    query: string;
    currentPosition: number;
    previousPosition: number;
    positionDelta: number;
    currentImpressions: number;
    previousImpressions: number;
  }>
> {
  const client = await getClient();

  const currentEnd = new Date();
  currentEnd.setDate(currentEnd.getDate() - 1);
  const currentStart = new Date(currentEnd);
  currentStart.setDate(currentStart.getDate() - days);

  const previousEnd = new Date(currentStart);
  previousEnd.setDate(previousEnd.getDate() - 1);
  const previousStart = new Date(previousEnd);
  previousStart.setDate(previousStart.getDate() - compareDays);

  console.log(`[GSC] Comparing keyword positions across periods...`);

  const [currentRes, previousRes] = await Promise.all([
    client.searchanalytics.query({
      siteUrl: GSC_SITE_URL,
      requestBody: {
        startDate: currentStart.toISOString().split('T')[0],
        endDate: currentEnd.toISOString().split('T')[0],
        dimensions: ['query'],
        rowLimit: 5000,
      },
    }),
    client.searchanalytics.query({
      siteUrl: GSC_SITE_URL,
      requestBody: {
        startDate: previousStart.toISOString().split('T')[0],
        endDate: previousEnd.toISOString().split('T')[0],
        dimensions: ['query'],
        rowLimit: 5000,
      },
    }),
  ]);

  const previousMap = new Map<string, { position: number; impressions: number }>();
  for (const row of previousRes.data.rows || []) {
    previousMap.set(row.keys![0], {
      position: row.position || 0,
      impressions: row.impressions || 0,
    });
  }

  const movers: Array<{
    query: string;
    currentPosition: number;
    previousPosition: number;
    positionDelta: number;
    currentImpressions: number;
    previousImpressions: number;
  }> = [];

  for (const row of currentRes.data.rows || []) {
    const query = row.keys![0];
    const prev = previousMap.get(query);
    if (prev) {
      const delta = prev.position - (row.position || 0); // positive = improved
      movers.push({
        query,
        currentPosition: row.position || 0,
        previousPosition: prev.position,
        positionDelta: delta,
        currentImpressions: row.impressions || 0,
        previousImpressions: prev.impressions,
      });
    }
  }

  movers.sort((a, b) => Math.abs(b.positionDelta) - Math.abs(a.positionDelta));
  console.log(`[GSC] ${movers.length} keywords with position changes`);
  return movers;
}
