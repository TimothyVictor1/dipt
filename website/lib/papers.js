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
