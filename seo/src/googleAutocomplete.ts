import {
  SEED_KEYWORDS,
  EXPANSION_SEEDS,
  LONG_TAIL_TOPICS,
  QUESTION_PREFIXES,
  REQUEST_DELAY_MS,
} from './config.js';
import { delay, normalizeKeyword } from './utils.js';

export interface AutocompleteKeyword {
  keyword: string;
  seedQuery: string;
  position: number;
  source: 'google_autocomplete';
}

const SUGGEST_URL = 'https://www.google.com/complete/search';

async function fetchSuggestions(query: string): Promise<string[]> {
  const params = new URLSearchParams({
    client: 'firefox',
    q: query,
  });

  try {
    const res = await fetch(`${SUGGEST_URL}?${params}`, {
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      },
    });

    if (!res.ok) {
      console.warn(`  Autocomplete failed for "${query}": ${res.status}`);
      return [];
    }

    const data = await res.json();
    return (data[1] as string[]) || [];
  } catch (err) {
    console.warn(`  Autocomplete error for "${query}": ${(err as Error).message}`);
    return [];
  }
}

export async function scrapeAutocomplete(
  seeds: string[] = SEED_KEYWORDS,
  expandSeeds: string[] = EXPANSION_SEEDS
): Promise<AutocompleteKeyword[]> {
  const results: AutocompleteKeyword[] = [];
  const seen = new Set<string>();

  console.log(`\n[Autocomplete] Fetching suggestions for ${seeds.length} seed keywords...`);

  // Phase 1: Direct suggestions for all seeds
  for (const seed of seeds) {
    await delay(REQUEST_DELAY_MS);
    const suggestions = await fetchSuggestions(seed);
    console.log(`  "${seed}" → ${suggestions.length} suggestions`);

    for (let i = 0; i < suggestions.length; i++) {
      const normalized = normalizeKeyword(suggestions[i]);
      if (!seen.has(normalized)) {
        seen.add(normalized);
        results.push({
          keyword: normalized,
          seedQuery: seed,
          position: i,
          source: 'google_autocomplete',
        });
      }
    }
  }

  // Phase 2: Alphabet expansion on high-value seeds
  console.log(
    `\n[Autocomplete] Expanding ${expandSeeds.length} seeds with alphabet suffixes...`
  );

  for (const seed of expandSeeds) {
    for (const letter of 'abcdefghijklmnopqrstuvwxyz') {
      const query = `${seed} ${letter}`;
      await delay(REQUEST_DELAY_MS);
      const suggestions = await fetchSuggestions(query);

      for (let i = 0; i < suggestions.length; i++) {
        const normalized = normalizeKeyword(suggestions[i]);
        if (!seen.has(normalized)) {
          seen.add(normalized);
          results.push({
            keyword: normalized,
            seedQuery: query,
            position: i,
            source: 'google_autocomplete',
          });
        }
      }
    }
    console.log(`  "${seed}" alphabet expansion complete`);
  }

  // Phase 3: Long-tail question mining — cross question prefixes with health topics
  // This is what surfaces "how to tell if you need more magnesium" type queries
  console.log(
    `\n[Autocomplete] Mining long-tail questions (${QUESTION_PREFIXES.length} prefixes × ${LONG_TAIL_TOPICS.length} topics)...`
  );

  let longTailCount = 0;
  for (const prefix of QUESTION_PREFIXES) {
    for (const topic of LONG_TAIL_TOPICS) {
      const query = `${prefix} ${topic}`;
      await delay(REQUEST_DELAY_MS);
      const suggestions = await fetchSuggestions(query);

      for (let i = 0; i < suggestions.length; i++) {
        const normalized = normalizeKeyword(suggestions[i]);
        if (!seen.has(normalized)) {
          seen.add(normalized);
          longTailCount++;
          results.push({
            keyword: normalized,
            seedQuery: query,
            position: i,
            source: 'google_autocomplete',
          });
        }
      }
    }
    console.log(`  "${prefix} ..." complete`);
  }

  console.log(`[Autocomplete] Long-tail questions found: ${longTailCount}`);
  console.log(`[Autocomplete] Total unique keywords: ${results.length}`);
  return results;
}
