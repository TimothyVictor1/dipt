import Link from 'next/link';
import Layout from '@/components/Layout';
import { getAllCategories } from '@/lib/papers';

export async function getStaticProps() {
  return { props: { categories: getAllCategories() } };
}

export default function CategoriesIndex({ categories }) {
  return (
    <Layout title="Categories">
      <h1>Categories</h1>
      <p className="muted">
        {categories.length} categor{categories.length === 1 ? 'y' : 'ies'} with
        at least one approved paper.
      </p>
      <ul className="category-list">
        {categories.map((c) => (
          <li key={c.slug}>
            <Link href={`/categories/${c.slug}/`}>{c.name}</Link>
            <span className="muted"> {c.count}</span>
          </li>
        ))}
      </ul>
    </Layout>
  );
}
