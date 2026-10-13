import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/router';
import Fuse from 'fuse.js';
import Layout from '@/components/Layout';
import PaperCard from '@/components/PaperCard';
import {
  getAllPapers,
  getAllCategories,
  getSearchIndex,
  getSources,
  getGeneratedAt,
} from '@/lib/papers';

export async function getStaticProps() {
  return {
    props: {
      papers: getAllPapers(),
      categories: getAllCategories(),
      sources: getSources(),
      index: getSearchIndex(),
      generatedAt: getGeneratedAt(),
    },
  };
}

const FUSE_OPTIONS = {
  includeScore: false,
  threshold: 0.38,
  ignoreLocation: true,
  keys: [
    { name: 'title', weight: 0.4 },
    { name: 'categories', weight: 0.2 },
    { name: 'problem', weight: 0.15 },
    { name: 'findings', weight: 0.15 },
    { name: 'authors', weight: 0.05 },
    { name: 'abstract', weight: 0.05 },
  ],
};

const SCORE_STEPS = [
  { v: 5, label: '5 and up' },
  { v: 7, label: '7 and up' },
  { v: 8, label: '8 and up' },
];

const CAT_LIMIT = 8;

export default function Home({
  papers,
  categories,
  sources,
  index,
  generatedAt,
}) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [minScore, setMinScore] = useState(0);
  const [source, setSource] = useState('');
  const [sort, setSort] = useState('score');
  const [showAllCats, setShowAllCats] = useState(false);

  useEffect(() => {
    if (typeof router.query.q === 'string') setQuery(router.query.q);
    if (typeof router.query.category === 'string')
      setCategory(router.query.category);
  }, [router.query.q, router.query.category]);

  const fuse = useMemo(() => new Fuse(index, FUSE_OPTIONS), [index]);
  const byId = useMemo(() => {
    const m = new Map();
    papers.forEach((p) => m.set(String(p.id), p));
    return m;
  }, [papers]);

  const results = useMemo(() => {
    let list = papers;
    if (query.trim()) {
      list = fuse
        .search(query.trim())
        .map((r) => byId.get(String(r.item.id)))
        .filter(Boolean);
    }
    if (category)
      list = list.filter((p) => (p.category_slugs ?? []).includes(category));
    if (source) list = list.filter((p) => p.source === source);
    if (minScore > 0) list = list.filter((p) => (p.score ?? 0) >= minScore);

    const sorted = [...list];
    if (sort === 'score')
      sorted.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    else if (sort === 'date')
      sorted.sort((a, b) =>
        String(b.published_date || '').localeCompare(
          String(a.published_date || '')
        )
      );
    else if (sort === 'title')
      sorted.sort((a, b) => a.title.localeCompare(b.title));
    return sorted;
  }, [papers, fuse, byId, query, category, source, minScore, sort]);

  const catName = category
    ? categories.find((c) => c.slug === category)?.name
    : null;
  const activeFilters = [
    query.trim() ? { key: 'q', label: `“${query.trim()}”`, clear: () => setQuery('') } : null,
    catName ? { key: 'c', label: catName, clear: () => setCategory('') } : null,
    minScore > 0
      ? { key: 's', label: `score ${minScore}+`, clear: () => setMinScore(0) }
      : null,
    source ? { key: 'src', label: source, clear: () => setSource('') } : null,
  ].filter(Boolean);

  const shownCats = showAllCats ? categories : categories.slice(0, CAT_LIMIT);

  return (
    <Layout>
      <form
        className="serp-search"
        role="search"
        onSubmit={(e) => e.preventDefault()}
      >
        <span className="search-icon" aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.6" />
            <path
              d="M11 11l3.5 3.5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
        </span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search papers, topics, authors…"
          aria-label="Search papers"
        />
      </form>

      <div className="serp-body">
        <main>
          <div className="serp-head">
            <span className="serp-count ox-num">
              {results.length} paper{results.length === 1 ? '' : 's'}
              {activeFilters.length ? ' · filtered' : ''}
            </span>
            {activeFilters.length ? (
              <div className="filter-chips">
                {activeFilters.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className="filter-chip"
                    onClick={f.clear}
                  >
                    {f.label}
                    <span className="x" aria-hidden="true">
                      ×
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
            <span className="spacer" />
            <label className="serp-sort">
              Sort
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="score">Relevance score</option>
                <option value="date">Newest</option>
                <option value="title">Title A–Z</option>
              </select>
            </label>
          </div>

          <div className="serp-results">
            {results.map((p) => (
              <PaperCard key={p.id} paper={p} />
            ))}
            {results.length === 0 ? (
              <p className="serp-empty">
                No papers match. Try adjusting your search or filters.
              </p>
            ) : null}
          </div>
        </main>

        <aside className="rail">
          <div className="groupby">
            <div className="groupby-title">By topic</div>
            <button
              type="button"
              className={`groupby-row${!category ? ' on' : ''}`}
              onClick={() => setCategory('')}
            >
              <span className="label">All topics</span>
              <span className="count">{papers.length}</span>
            </button>
            {shownCats.map((c) => (
              <button
                key={c.slug}
                type="button"
                className={`groupby-row${category === c.slug ? ' on' : ''}`}
                onClick={() =>
                  setCategory(category === c.slug ? '' : c.slug)
                }
              >
                <span className="label">{c.name}</span>
                <span className="count">{c.count}</span>
              </button>
            ))}
            {categories.length > CAT_LIMIT ? (
              <button
                type="button"
                className="groupby-more"
                onClick={() => setShowAllCats((s) => !s)}
              >
                {showAllCats
                  ? 'Show fewer'
                  : `Show all ${categories.length}`}
              </button>
            ) : null}
          </div>

          <div className="groupby">
            <div className="groupby-title">By industrial relevance</div>
            <button
              type="button"
              className={`groupby-row${minScore === 0 ? ' on' : ''}`}
              onClick={() => setMinScore(0)}
            >
              <span className="label">Any score</span>
              <span className="count">{papers.length}</span>
            </button>
            {SCORE_STEPS.map((s) => (
              <button
                key={s.v}
                type="button"
                className={`groupby-row${minScore === s.v ? ' on' : ''}`}
                onClick={() => setMinScore(minScore === s.v ? 0 : s.v)}
              >
                <span className="label">{s.label}</span>
                <span className="count">
                  {papers.filter((p) => (p.score ?? 0) >= s.v).length}
                </span>
              </button>
            ))}
          </div>

          {sources.length > 1 ? (
            <div className="groupby">
              <div className="groupby-title">By source</div>
              <button
                type="button"
                className={`groupby-row${!source ? ' on' : ''}`}
                onClick={() => setSource('')}
              >
                <span className="label">All sources</span>
                <span className="count">{papers.length}</span>
              </button>
              {sources.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`groupby-row${source === s ? ' on' : ''}`}
                  onClick={() => setSource(source === s ? '' : s)}
                >
                  <span className="label">{s}</span>
                  <span className="count">
                    {papers.filter((p) => p.source === s).length}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          {generatedAt ? (
            <p
              className="summary-note"
              style={{ padding: '0 8px', marginTop: 4 }}
            >
              Updated {new Date(generatedAt).toISOString().slice(0, 10)}.
            </p>
          ) : null}
        </aside>
      </div>
    </Layout>
  );
}
