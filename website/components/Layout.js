import Head from 'next/head';
import Link from 'next/link';

/**
 * Page chrome shared by every route: head tags, header nav, footer.
 */
export default function Layout({ children, title, description }) {
  const fullTitle = title ? `${title} - DIPT` : 'DIPT - SE Research Monitor';
  return (
    <>
      <Head>
        <title>{fullTitle}</title>
        <meta
          name="description"
          content={
            description ??
            'Newly approved Software Engineering research, summarised and scored for industrial relevance.'
          }
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link
          rel="alternate"
          type="application/rss+xml"
          title="DIPT approved papers"
          href="/rss.xml"
        />
      </Head>
      <header className="site-header">
        <div className="wrap">
          <Link href="/" className="brand">
            DIPT
          </Link>
          <nav>
            <Link href="/">Papers</Link>
            <Link href="/categories/">Categories</Link>
            <a href="/rss.xml">RSS</a>
          </nav>
        </div>
      </header>
      <main className="wrap">{children}</main>
      <footer className="site-footer">
        <div className="wrap">
          <p>
            DIPT - automated Software Engineering research monitoring. SERL
            Sweden, Blekinge Institute of Technology.
          </p>
        </div>
      </footer>
    </>
  );
}
