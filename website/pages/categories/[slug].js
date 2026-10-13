import Layout from '@/components/Layout';
import Breadcrumb from '@/components/Breadcrumb';
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
      <Breadcrumb
        items={[
          { label: 'Papers', href: '/' },
          { label: 'Topics', href: '/categories/' },
          { label: category.name },
        ]}
      />
      <h1 className="section-title" style={{ marginTop: 12 }}>
        {category.name}
      </h1>
      <p className="page-sub" style={{ marginBottom: 16 }}>
        {papers.length} approved paper{papers.length === 1 ? '' : 's'}, highest
        industrial-relevance score first.
      </p>
      <div className="serp-results">
        {papers.map((p) => (
          <PaperCard key={p.id} paper={p} />
        ))}
      </div>
    </Layout>
  );
}
