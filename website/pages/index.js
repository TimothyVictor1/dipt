import { useMemo, useState } from 'react';
import Fuse from 'fuse.js';
import Layout from '@/components/Layout';
import PaperCard from '@/components/PaperCard';
import {
  getAllPapers,
  getAllCategories,
  getSearchIndex,
  getGeneratedAt,
} from '@/lib/papers';

export async function getStaticProps() {
  return {
    props: {
      papers: getAllPapers(),
      categories: getAllCategories(),
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

export default function Home({ papers, categories, index, generatedAt }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [minScore, setMinScore] = useState(0);

  const fuse = useMemo(() => new Fuse(index, FUSE_OPTIONS), [index]);
  const byId = useMemo(() => {
    const m = new Map();
    papers.forEach((p) => m.set(String(p.id), p));
    return m;
  }, [papers]);

  const results = useMemo(() => {
    let list = papers;
    if (query.trim()) {
      list = fuse.search(query.trim()).map((r) => byId.get(String(r.item.id)));
    }
    if (category) {
      list = list.filter((p) => (p.category_slugs ?? []).includes(category));
    }
    if (minScore > 0) {
      list = list.filter((p) => (p.score ?? 0) >= minScore);
    }
    return list.filter(Boolean);
  }, [papers, fuse, byId, query, category, minScore]);

  return (
    <Layout>
      <section className="hero">
        <h1>Approved Software Engineering research</h1>
        <p>
          {papers.length} paper{papers.length === 1 ? '' : 's'}, each read,
          categorised, summarised and scored for industrial relevance by a local
          pipeline.
          {generatedAt ? (
            <>
              {' '}
              <span className="muted">
                Updated {new Date(generatedAt).toISOString().slice(0, 10)}.
              </span>
            </>
          ) : null}
        </p>
      </section>

      <section className="controls">
        <input
          type="search"
          placeholder="Search titles, topics, findings, authors..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search papers"
        />
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Filter by category"
        >
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>
        <label className="score-filter">
          Min score: {minScore}
          <input
            type="range"
            min="0"
            max="10"
            step="1"
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
          />
        </label>
      </section>

      <p className="muted count-line">
        {results.length} shown
        {query || category || minScore ? ' (filtered)' : ''}
      </p>

      <div className="list">
        {results.map((p) => (
          <PaperCard key={p.id} paper={p} />
        ))}
        {results.length === 0 ? (
          <p className="empty">No papers match. Try a broader search.</p>
        ) : null}
      </div>
    </Layout>
  );
}
