import { google } from 'googleapis';
import { BetaAnalyticsDataClient } from '@google-analytics/data';

const keyPath = process.env.GOOGLE_SERVICE_ACCOUNT_KEY_PATH || '';
const siteUrl = process.env.GSC_SITE_URL || '';
const ga4Property = process.env.GA4_PROPERTY_ID || '';

async function testGSC() {
  console.log('--- Google Search Console ---');
  try {
    const auth = new google.auth.GoogleAuth({
      keyFile: keyPath,
      scopes: ['https://www.googleapis.com/auth/webmasters.readonly'],
    });

    const client = google.searchconsole({ version: 'v1', auth });
    const sites = await client.sites.list();
    const siteList = sites.data.siteEntry?.map(s => s.siteUrl) || [];
    console.log('Auth: OK');
    console.log('Accessible sites:', siteList.length > 0 ? siteList.join(', ') : '(none)');

    if (siteList.length > 0) {
      // Try a small query
      const end = new Date();
      end.setDate(end.getDate() - 2);
      const start = new Date(end);
      start.setDate(start.getDate() - 7);

      const res = await client.searchanalytics.query({
        siteUrl,
        requestBody: {
          startDate: start.toISOString().split('T')[0],
          endDate: end.toISOString().split('T')[0],
          dimensions: ['query'],
          rowLimit: 5,
        },
      });

      const rows = res.data.rows || [];
      console.log(`Sample query (last 7 days): ${rows.length} keywords returned`);
      for (const row of rows.slice(0, 3)) {
        console.log(`  "${row.keys![0]}" — pos ${row.position?.toFixed(1)}, ${row.impressions} imp, ${row.clicks} clicks`);
      }
    }
  } catch (err: any) {
    console.error('FAILED:', err.message);
    if (err.code === 403) console.error('  → Service account not added to Search Console, or API not enabled');
    if (err.code === 401) console.error('  → Invalid credentials');
  }
}

async function testGA4() {
  console.log('\n--- Google Analytics 4 ---');
  if (!ga4Property) {
    console.log('Skipped: GA4_PROPERTY_ID not set');
    return;
  }

  try {
    const client = new BetaAnalyticsDataClient({ keyFilename: keyPath });

    const [response] = await client.runReport({
      property: `properties/${ga4Property}`,
      dateRanges: [{ startDate: '7daysAgo', endDate: 'yesterday' }],
      dimensions: [{ name: 'pagePath' }],
      metrics: [{ name: 'screenPageViews' }],
      limit: 5,
      orderBys: [{ metric: { metricName: 'screenPageViews' }, desc: true }],
    });

    console.log('Auth: OK');
    const rows = response.rows || [];
    console.log(`Sample (last 7 days): ${rows.length} pages returned`);
    for (const row of rows.slice(0, 3)) {
      console.log(`  ${row.dimensionValues?.[0]?.value} — ${row.metricValues?.[0]?.value} pageviews`);
    }
  } catch (err: any) {
    console.error('FAILED:', err.message);
    if (err.message.includes('permission')) console.error('  → Service account not added to GA4 property');
    if (err.message.includes('not found')) console.error('  → Check GA4_PROPERTY_ID is correct');
  }
}

async function main() {
  console.log(`Key file: ${keyPath}`);
  console.log(`GSC site: ${siteUrl}`);
  console.log(`GA4 property: ${ga4Property || '(not set)'}\n`);

  await testGSC();
  await testGA4();
}

main().catch(console.error);
