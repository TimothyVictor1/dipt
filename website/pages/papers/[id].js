import Link from 'next/link';
import Layout from '@/components/Layout';
import { getAllPapers, getPaperById } from '@/lib/papers';

export async function getStaticPaths() {
  return {
    paths: getAllPapers().map((p) => ({ params: { id: String(p.id) } })),
    fallback: false,
  };
}

export async function getStaticProps({ params }) {
  const paper = getPaperById(params.id);
  if (!paper) return { notFound: true };
  return { props: { paper } };
}

const SECTIONS = [
  ['research_problem', 'Research problem'],
  ['methodology', 'Methodology'],
  ['key_findings', 'Key findings'],
  ['industrial_implications', 'Industrial implications'],
];

export default function PaperPage({ paper }) {
  return (
    <Layout title={paper.title} description={paper.summary?.research_problem}>
      <article className="detail">
        <p className="back">
          <Link href="/">&larr; All papers</Link>
        </p>
        <h1>{paper.title}</h1>
        <p className="meta">
          {(paper.authors ?? []).join(', ')}
          {paper.published_date ? ` · ${paper.published_date}` : ''}
          {paper.source ? ` · ${paper.source}` : ''}
          {paper.doi ? (
            <>
              {' · '}
              <a
                href={`https://doi.org/${paper.doi}`}
                target="_blank"
                rel="noreferrer"
              >
                doi:{paper.doi}
              </a>
            </>
          ) : null}
        </p>

        {paper.score !== null && paper.score !== undefined ? (
          <div className="score-block">
            <span
              className={`score score-${
                paper.score >= 8 ? 'high' : paper.score >= 5 ? 'mid' : 'low'
              }`}
            >
              {Number(paper.score).toFixed(1)} / 10
            </span>
            <span className="muted"> industrial relevance</span>
            {paper.score_rationale ? (
              <p className="rationale">{paper.score_rationale}</p>
            ) : null}
          </div>
        ) : null}

        <ul className="tags">
          {(paper.categories ?? []).map((name, i) => (
            <li key={i}>
              <Link href={`/categories/${paper.category_slugs[i]}/`}>
                {name}
              </Link>
            </li>
          ))}
        </ul>

        <section className="summary">
          {SECTIONS.map(([key, label]) =>
            paper.summary?.[key] ? (
              <div key={key}>
                <h2>{label}</h2>
                <p>{paper.summary[key]}</p>
              </div>
            ) : null
          )}
          {!SECTIONS.some(([k]) => paper.summary?.[k]) && paper.summary_text ? (
            <pre className="summary-raw">{paper.summary_text}</pre>
          ) : null}
        </section>

        {paper.abstract ? (
          <details className="abstract">
            <summary>Original abstract</summary>
            <p>{paper.abstract}</p>
          </details>
        ) : null}
      </article>
    </Layout>
  );
}
