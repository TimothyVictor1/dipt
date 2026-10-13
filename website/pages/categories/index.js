import Link from 'next/link';
import Layout from '@/components/Layout';
import Breadcrumb from '@/components/Breadcrumb';
import { getAllCategories } from '@/lib/papers';

export async function getStaticProps() {
  return { props: { categories: getAllCategories() } };
}

export default function CategoriesIndex({ categories }) {
  return (
    <Layout title="Topics">
      <div className="prose">
        <Breadcrumb
          items={[{ label: 'Papers', href: '/' }, { label: 'Topics' }]}
        />
        <h1>Topics</h1>
        <p className="page-sub">
          {categories.length} topic{categories.length === 1 ? '' : 's'} with at
          least one approved paper.
        </p>
        <ul className="cat-list">
          {categories.map((c) => (
            <li key={c.slug}>
              <Link href={`/categories/${c.slug}/`}>
                <span className="cat-name">{c.name}</span>
                <span className="cat-count ox-num">{c.count}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </Layout>
  );
}
