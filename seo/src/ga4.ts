import { BetaAnalyticsDataClient } from '@google-analytics/data';
import {
  BLOG_PATH_PREFIX,
  GA4_PROPERTY_ID,
  GOOGLE_SERVICE_ACCOUNT_KEY_PATH,
} from './config.js';

export interface GA4PageData {
  path: string;
  pageviews: number;
  uniqueUsers: number;
  avgSessionDuration: number;
  bounceRate: number;
  organicTraffic: number;
  conversions: number;
}

export interface GA4TrafficSource {
  path: string;
  source: string;
  medium: string;
  sessions: number;
}

function getClient(): BetaAnalyticsDataClient {
  if (!GOOGLE_SERVICE_ACCOUNT_KEY_PATH) {
    throw new Error(
      'GOOGLE_SERVICE_ACCOUNT_KEY_PATH not set. Provide the path to your service account JSON key.'
    );
  }
  if (!GA4_PROPERTY_ID) {
    throw new Error(
      'GA4_PROPERTY_ID not set. Provide your GA4 property ID (e.g., "123456789").'
    );
  }

  return new BetaAnalyticsDataClient({
    keyFilename: GOOGLE_SERVICE_ACCOUNT_KEY_PATH,
  });
}

function formatDateRange(days: number): { startDate: string; endDate: string } {
  return { startDate: `${days}daysAgo`, endDate: 'yesterday' };
}

function blogPathPrefix(): string {
  return BLOG_PATH_PREFIX.replace(/\/$/, '') + '/';
}

export async function getBlogTraffic(days: number = 28): Promise<GA4PageData[]> {
  const client = getClient();
  const { startDate, endDate } = formatDateRange(days);

  console.log(`[GA4] Fetching blog traffic (last ${days} days)...`);

  const [response] = await client.runReport({
    property: `properties/${GA4_PROPERTY_ID}`,
    dateRanges: [{ startDate, endDate }],
    dimensions: [{ name: 'pagePath' }],
    metrics: [
      { name: 'screenPageViews' },
      { name: 'totalUsers' },
      { name: 'averageSessionDuration' },
      { name: 'bounceRate' },
    ],
    dimensionFilter: {
      filter: {
        fieldName: 'pagePath',
        stringFilter: {
          matchType: 'BEGINS_WITH',
          value: blogPathPrefix(),
        },
      },
    },
    limit: 500,
    orderBys: [{ metric: { metricName: 'screenPageViews' }, desc: true }],
  });

  const results: GA4PageData[] = [];

  for (const row of response.rows || []) {
    results.push({
      path: row.dimensionValues?.[0]?.value || '',
      pageviews: parseInt(row.metricValues?.[0]?.value || '0'),
      uniqueUsers: parseInt(row.metricValues?.[1]?.value || '0'),
      avgSessionDuration: parseFloat(row.metricValues?.[2]?.value || '0'),
      bounceRate: parseFloat(row.metricValues?.[3]?.value || '0'),
      organicTraffic: 0, // populated below
      conversions: 0, // populated below
    });
  }

  // Fetch organic traffic breakdown separately
  const [organicRes] = await client.runReport({
    property: `properties/${GA4_PROPERTY_ID}`,
    dateRanges: [{ startDate, endDate }],
    dimensions: [{ name: 'pagePath' }, { name: 'sessionDefaultChannelGroup' }],
    metrics: [{ name: 'sessions' }],
    dimensionFilter: {
      andGroup: {
        expressions: [
          {
            filter: {
              fieldName: 'pagePath',
              stringFilter: { matchType: 'BEGINS_WITH', value: blogPathPrefix() },
            },
          },
          {
            filter: {
              fieldName: 'sessionDefaultChannelGroup',
              stringFilter: { matchType: 'EXACT', value: 'Organic Search' },
            },
          },
        ],
      },
    },
    limit: 500,
  });

  const organicMap = new Map<string, number>();
  for (const row of organicRes.rows || []) {
    const path = row.dimensionValues?.[0]?.value || '';
    organicMap.set(path, parseInt(row.metricValues?.[0]?.value || '0'));
  }

  for (const result of results) {
    result.organicTraffic = organicMap.get(result.path) || 0;
  }

  console.log(`[GA4] ${results.length} blog pages with traffic data`);
  return results;
}

export async function getContentEngagement(
  paths: string[],
  days: number = 28
): Promise<GA4PageData[]> {
  const client = getClient();
  const { startDate, endDate } = formatDateRange(days);

  console.log(`[GA4] Fetching engagement for ${paths.length} pages...`);

  const [response] = await client.runReport({
    property: `properties/${GA4_PROPERTY_ID}`,
    dateRanges: [{ startDate, endDate }],
    dimensions: [{ name: 'pagePath' }],
    metrics: [
      { name: 'screenPageViews' },
      { name: 'totalUsers' },
      { name: 'averageSessionDuration' },
      { name: 'bounceRate' },
    ],
    dimensionFilter: {
      orGroup: {
        expressions: paths.map(path => ({
          filter: {
            fieldName: 'pagePath',
            stringFilter: { matchType: 'EXACT', value: path },
          },
        })),
      },
    },
  });

  return (response.rows || []).map(row => ({
    path: row.dimensionValues?.[0]?.value || '',
    pageviews: parseInt(row.metricValues?.[0]?.value || '0'),
    uniqueUsers: parseInt(row.metricValues?.[1]?.value || '0'),
    avgSessionDuration: parseFloat(row.metricValues?.[2]?.value || '0'),
    bounceRate: parseFloat(row.metricValues?.[3]?.value || '0'),
    organicTraffic: 0,
    conversions: 0,
  }));
}

export async function getTrafficSources(
  days: number = 28
): Promise<GA4TrafficSource[]> {
  const client = getClient();
  const { startDate, endDate } = formatDateRange(days);

  console.log(`[GA4] Fetching traffic sources for blog content...`);

  const [response] = await client.runReport({
    property: `properties/${GA4_PROPERTY_ID}`,
    dateRanges: [{ startDate, endDate }],
    dimensions: [
      { name: 'pagePath' },
      { name: 'sessionSource' },
      { name: 'sessionMedium' },
    ],
    metrics: [{ name: 'sessions' }],
    dimensionFilter: {
      filter: {
        fieldName: 'pagePath',
        stringFilter: { matchType: 'BEGINS_WITH', value: blogPathPrefix() },
      },
    },
    limit: 1000,
    orderBys: [{ metric: { metricName: 'sessions' }, desc: true }],
  });

  return (response.rows || []).map(row => ({
    path: row.dimensionValues?.[0]?.value || '',
    source: row.dimensionValues?.[1]?.value || '',
    medium: row.dimensionValues?.[2]?.value || '',
    sessions: parseInt(row.metricValues?.[0]?.value || '0'),
  }));
}

export async function getConversionsByContent(
  days: number = 28,
  conversionEventName: string = 'sign_up'
): Promise<Map<string, number>> {
  const client = getClient();
  const { startDate, endDate } = formatDateRange(days);

  console.log(`[GA4] Fetching conversions (event: ${conversionEventName})...`);

  const [response] = await client.runReport({
    property: `properties/${GA4_PROPERTY_ID}`,
    dateRanges: [{ startDate, endDate }],
    dimensions: [{ name: 'pagePath' }],
    metrics: [{ name: 'conversions' }],
    dimensionFilter: {
      andGroup: {
        expressions: [
          {
            filter: {
              fieldName: 'pagePath',
              stringFilter: { matchType: 'BEGINS_WITH', value: blogPathPrefix() },
            },
          },
          {
            filter: {
              fieldName: 'eventName',
              stringFilter: { matchType: 'EXACT', value: conversionEventName },
            },
          },
        ],
      },
    },
    limit: 500,
  });

  const conversions = new Map<string, number>();
  for (const row of response.rows || []) {
    const path = row.dimensionValues?.[0]?.value || '';
    conversions.set(path, parseInt(row.metricValues?.[0]?.value || '0'));
  }

  console.log(`[GA4] ${conversions.size} pages with conversions`);
  return conversions;
}
