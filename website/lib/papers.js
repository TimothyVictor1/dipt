/**
 * Build-time data access.
 *
 * `data/papers.json` and `data/categories.json` are written by
 * `python -m dipt.site_export`. These helpers are only ever called from
 * `getStaticProps` / `getStaticPaths`, so the JSON is read once at build time
 * and inlined into the static pages; the browser never fetches it.
 */
import papersData from '@/data/papers.json';
import categoriesData from '@/data/categories.json';

export function getGeneratedAt() {
  return papersData.generated_at ?? null;
}

export function getAllPapers() {
  return papersData.papers ?? [];
}

export function getPaperById(id) {
  const wanted = String(id);
  return getAllPapers().find((p) => String(p.id) === wanted) ?? null;
}

export function getAllCategories() {
  return categoriesData ?? [];
}

export function getCategoryBySlug(slug) {
  return getAllCategories().find((c) => c.slug === slug) ?? null;
}

export function getPapersInCategory(slug) {
  return getAllPapers().filter((p) => (p.category_slugs ?? []).includes(slug));
}

/** Distinct source names present in the data, e.g. ['arxiv', 'openalex']. */
export function getSources() {
  return [...new Set(getAllPapers().map((p) => p.source).filter(Boolean))].sort();
}

/**
 * Papers most related to `id`: those sharing the most categories, then the
 * highest scored. Excludes the paper itself.
 */
export function getRelatedPapers(id, limit = 6) {
  const self = getPaperById(id);
  if (!self) return [];
  const mine = new Set(self.category_slugs ?? []);
  return getAllPapers()
    .filter((p) => String(p.id) !== String(id))
    .map((p) => ({
      p,
      shared: (p.category_slugs ?? []).filter((s) => mine.has(s)).length,
    }))
    .filter((x) => x.shared > 0)
    .sort(
      (a, b) => b.shared - a.shared || (b.p.score ?? 0) - (a.p.score ?? 0)
    )
    .slice(0, limit)
    .map((x) => x.p);
}

/**
 * The compact record the client-side search index is built from. Keeping this
 * small keeps the inlined JSON on the home page small.
 */
export function getSearchIndex() {
  return getAllPapers().map((p) => ({
    id: p.id,
    title: p.title,
    authors: (p.authors ?? []).join(', '),
    categories: (p.categories ?? []).join(', '),
    abstract: p.abstract ?? '',
    problem: p.summary?.research_problem ?? '',
    findings: p.summary?.key_findings ?? '',
    score: p.score ?? null,
    published_date: p.published_date ?? null,
  }));
}
