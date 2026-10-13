import Link from 'next/link';

/**
 * Industrial-relevance score badge. Soft semantic colour: green 8+, amber 5-7,
 * neutral below. `withLabel` appends " / 10" for the detail-page metric.
 */
export function ScoreChip({ score, withLabel = false }) {
  if (score === null || score === undefined) return null;
  const band = score >= 8 ? 'high' : score >= 5 ? 'mid' : 'low';
  return (
    <span className={`score score-${band}`} title="Industrial-relevance score">
      {Number(score).toFixed(1)}
      {withLabel ? <span className="score-cap">&nbsp;/ 10</span> : null}
    </span>
  );
}

/**
 * One result row in the SERP list: title, meta line, topic tags, score aside.
 * `compact` is used by the related-papers rail — same shape, tighter tag cap.
 */
export default function PaperCard({ paper, compact = false }) {
  const authors = paper.authors ?? [];
  const cats = paper.categories ?? [];
  const tagCap = compact ? 2 : 3;

  return (
    <article className="result-row">
      <div className="result-main">
        <Link href={`/papers/${paper.id}/`} className="result-title">
          {paper.title}
        </Link>
        <div className="result-meta">
          {authors.slice(0, 3).join(', ')}
          {authors.length > 3 ? ' et al.' : ''}
          {paper.published_date ? ` · ${paper.published_date}` : ''}
          {paper.source ? ` · ${paper.source}` : ''}
        </div>
        {cats.length ? (
          <div className="result-tags">
            {cats.slice(0, tagCap).join(', ')}
            {cats.length > tagCap ? ` +${cats.length - tagCap}` : ''}
          </div>
        ) : null}
      </div>
      <div className="result-aside">
        <ScoreChip score={paper.score} />
      </div>
    </article>
  );
}
