export function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function normalizeKeyword(kw: string): string {
  return kw.toLowerCase().trim().replace(/\s+/g, ' ');
}

const STOP_WORDS = new Set([
  'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
  'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
  'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
  'as', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
  'between', 'out', 'off', 'over', 'under', 'again', 'further', 'then',
  'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'both',
  'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
  'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just',
  'don', 'now', 'and', 'but', 'or', 'if', 'it', 'its', 'i', 'me', 'my',
  'we', 'our', 'you', 'your', 'he', 'she', 'they', 'them', 'this', 'that',
  'these', 'those', 'what', 'which', 'who', 'whom', 'am', 'up', 'about',
]);

export function extractNgrams(text: string, n: number): string[] {
  const words = text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter(w => w.length > 1 && !STOP_WORDS.has(w));

  const ngrams: string[] = [];
  for (let i = 0; i <= words.length - n; i++) {
    ngrams.push(words.slice(i, i + n).join(' '));
  }
  return ngrams;
}

export function countFrequencies(items: string[]): Map<string, number> {
  const freq = new Map<string, number>();
  for (const item of items) {
    freq.set(item, (freq.get(item) || 0) + 1);
  }
  return freq;
}

export function generateCSV(
  keywords: Array<{
    keyword: string;
    score: number;
    sources: string[];
    category: string;
    intent: string;
  }>
): string {
  const header = 'keyword,score,sources,category,intent';
  const rows = keywords.map(k => {
    const escaped = (s: string) => `"${s.replace(/"/g, '""')}"`;
    return [
      escaped(k.keyword),
      k.score.toFixed(1),
      escaped(k.sources.join('; ')),
      escaped(k.category),
      escaped(k.intent),
    ].join(',');
  });
  return [header, ...rows].join('\n');
}

export function generateEvaluationCSV(report: {
  quickWins: Array<{ keyword: string; position: number; impressions: number; action: string }>;
  ctrOpportunities: Array<{ keyword: string; position: number; currentCTR: number; expectedCTR: number; impressions: number }>;
  contentGaps: Array<{ keyword: string; seoScore: number; category: string; intent: string }>;
  underperformers: Array<{ slug: string; title: string; daysSincePublish: number; impressions: number }>;
  decliners: Array<{ keyword: string; currentPosition: number; previousPosition: number; positionDelta: number }>;
}): string {
  const escaped = (s: string) => `"${String(s).replace(/"/g, '""')}"`;
  const lines: string[] = [];

  lines.push('bucket,keyword_or_slug,metric1,metric2,metric3,category_or_action');

  for (const qw of report.quickWins) {
    lines.push(
      ['quick_win', escaped(qw.keyword), qw.position.toFixed(1), qw.impressions, '', escaped(qw.action)].join(',')
    );
  }
  for (const ctr of report.ctrOpportunities) {
    lines.push(
      ['ctr_opportunity', escaped(ctr.keyword), ctr.position.toFixed(1), (ctr.currentCTR * 100).toFixed(1) + '%', (ctr.expectedCTR * 100).toFixed(0) + '%', ctr.impressions].join(',')
    );
  }
  for (const gap of report.contentGaps) {
    lines.push(
      ['content_gap', escaped(gap.keyword), gap.seoScore.toFixed(1), '', '', escaped(gap.category)].join(',')
    );
  }
  for (const up of report.underperformers) {
    lines.push(
      ['underperformer', escaped(up.slug), up.daysSincePublish + 'd', up.impressions, '', escaped(up.title)].join(',')
    );
  }
  for (const d of report.decliners) {
    lines.push(
      ['declining', escaped(d.keyword), d.previousPosition.toFixed(1), d.currentPosition.toFixed(1), d.positionDelta.toFixed(1), ''].join(',')
    );
  }

  return lines.join('\n');
}
