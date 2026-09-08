import Link from 'next/link';
import Layout from '@/components/Layout';
import PaperCard from '@/components/PaperCard';
import {
  getAllCategories,
  getCategoryBySlug,
  getPapersInCategory,
} from '@/lib/papers';

export async function getStaticPaths() {
  return {
    paths: getAllCategories().map((c) => ({ params: { slug: c.slug } })),
    fallback: false,
  };
}

export async function getStaticProps({ params }) {
  const category = getCategoryBySlug(params.slug);
  if (!category) return { notFound: true };
  const papers = getPapersInCategory(params.slug).sort(
    (a, b) => (b.score ?? 0) - (a.score ?? 0)
  );
  return { props: { category, papers } };
}

export default function CategoryPage({ category, papers }) {
  return (
    <Layout title={category.name}>
      <p className="back">
        <Link href="/categories/">&larr; All categories</Link>
      </p>
      <h1>{category.name}</h1>
      <p className="muted">
        {papers.length} approved paper{papers.length === 1 ? '' : 's'}
      </p>
      <div className="list">
        {papers.map((p) => (
          <PaperCard key={p.id} paper={p} />
        ))}
      </div>
    </Layout>
  );
}
