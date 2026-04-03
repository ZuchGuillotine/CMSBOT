import { existsSync, readFileSync } from 'node:fs';

interface CompetitorConfig {
  name: string;
  domain: string;
  sitemapPath: string;
}

interface SEOProjectConfig {
  seed_keywords?: string[];
  expansion_seeds?: string[];
  long_tail_topics?: string[];
  question_prefixes?: string[];
  buyer_intent_signals?: string[];
  subreddits?: string[];
  competitors?: CompetitorConfig[];
  relevance_terms?: string[];
  blog_path_prefix?: string;
  blog_api_url?: string;
  gsc_site_url?: string;
  ga4_property_id?: string;
  google_service_account_key_path?: string;
  conversion_event_name?: string;
}

interface ProjectProfile {
  seo?: SEOProjectConfig;
}

function loadProjectProfile(): ProjectProfile {
  const path = process.env.CMSBOT_PROJECT_FILE;
  if (!path) return {};
  if (!existsSync(path)) {
    throw new Error(`CMSBOT_PROJECT_FILE does not exist: ${path}`);
  }

  const raw = JSON.parse(readFileSync(path, 'utf-8'));
  return typeof raw === 'object' && raw ? raw : {};
}

const project = loadProjectProfile();
const seo = project.seo || {};

export const SEED_KEYWORDS = seo.seed_keywords || [];
export const EXPANSION_SEEDS = seo.expansion_seeds || [];
export const LONG_TAIL_TOPICS = seo.long_tail_topics || [];
export const QUESTION_PREFIXES = seo.question_prefixes || [
  'how to',
  'best way to',
  'what is',
  'why does',
];

export const BUYER_INTENT_SIGNALS = seo.buyer_intent_signals || [
  'best',
  'top',
  'vs',
  'compare',
  'alternative',
  'review',
  'pricing',
  'software',
  'tool',
  'platform',
  'service',
];

export const SUBREDDITS = seo.subreddits || [];

export const COMPETITORS = seo.competitors || [];

export const RELEVANCE_TERMS = seo.relevance_terms || [];

export const REQUEST_DELAY_MS = 1200;
export const REDDIT_DELAY_MS = 2000;

export const BLOG_PATH_PREFIX = seo.blog_path_prefix || '/blog/';

export const GSC_SITE_URL = process.env.GSC_SITE_URL || seo.gsc_site_url || '';
export const GOOGLE_SERVICE_ACCOUNT_KEY_PATH =
  process.env.GOOGLE_SERVICE_ACCOUNT_KEY_PATH ||
  seo.google_service_account_key_path ||
  '';

export const GA4_PROPERTY_ID =
  process.env.GA4_PROPERTY_ID || seo.ga4_property_id || '';

export const CONVERSION_EVENT_NAME =
  process.env.GA4_CONVERSION_EVENT || seo.conversion_event_name || 'sign_up';

export const EVALUATION_DAYS = 28;
export const COMPARE_DAYS = 28;
export const BLOG_API_URL =
  process.env.BLOG_API_URL || seo.blog_api_url || '';

export const EXPECTED_CTR_BY_POSITION: Record<number, number> = {
  1: 0.30, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05,
  6: 0.04, 7: 0.03, 8: 0.025, 9: 0.02, 10: 0.015,
};
