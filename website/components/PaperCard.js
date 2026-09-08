import Link from 'next/link';

/**
 * Score pill: green for 8+, amber for 5-7, grey below.
 */
function ScoreBadge({ score }) {
  if (score === null || score === undefined) return null;
  const band = score >= 8 ? 'high' : score >= 5 ? 'mid' : 'low';
  return (
    <span className={`score score-${band}`} title="Industrial relevance score">
      {Number(score).toFixed(1)}
    </span>
  );
}

/**
 * One paper in a list: title, meta line, categories, and the research-problem
 * sentence from the summary as a teaser.
 */
export default function PaperCard({ paper }) {
  return (
    <article className="card">
      <div className="card-head">
        <h2>
          <Link href={`/papers/${paper.id}/`}>{paper.title}</Link>
        </h2>
        <ScoreBadge score={paper.score} />
      </div>
      <p className="meta">
        {(paper.authors ?? []).slice(0, 4).join(', ')}
        {(paper.authors ?? []).length > 4 ? ' et al.' : ''}
        {paper.published_date ? ` · ${paper.published_date}` : ''}
        {paper.source ? ` · ${paper.source}` : ''}
      </p>
      {paper.summary?.research_problem ? (
        <p className="teaser">{paper.summary.research_problem}</p>
      ) : (
        <p className="teaser">{paper.abstract?.slice(0, 240)}</p>
      )}
      <ul className="tags">
        {(paper.categories ?? []).map((name, i) => (
          <li key={i}>
            <Link href={`/categories/${paper.category_slugs[i]}/`}>{name}</Link>
          </li>
        ))}
      </ul>
    </article>
  );
}
