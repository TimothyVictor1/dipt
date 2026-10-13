import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useEffect, useState } from 'react';

/**
 * Page chrome: head tags, a minimal OpenAlex-style top bar (brand + nav only —
 * search is the hero on the results page, not in the bar), and a footer.
 *
 * The footer carries a discreet "operator mode" toggle. It is the only thing
 * that reveals the per-paper Share panel; a visitor arriving from a shared
 * post never sees it.
 */
export default function Layout({ children, title, description }) {
  const router = useRouter();
  const [operator, setOperator] = useState(false);

  useEffect(() => {
    try {
      setOperator(localStorage.getItem('dipt-operator') === '1');
    } catch {
      setOperator(false);
    }
  }, []);

  const fullTitle = title
    ? `${title} — DIPT`
    : 'DIPT — Software Engineering research monitor';

  function toggleOperator() {
    try {
      const next = operator ? '0' : '1';
      localStorage.setItem('dipt-operator', next);
      setOperator(next === '1');
      router.reload();
    } catch {
      /* storage unavailable */
    }
  }

  return (
    <>
      <Head>
        <title>{fullTitle}</title>
        <meta
          name="description"
          content={
            description ??
            'Recent Software Engineering research, read and summarised, scored for industrial relevance.'
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

      <header className="appbar">
        <div className="appbar-inner">
          <Link href="/" className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span>DIPT</span>
          </Link>
          <nav className="appbar-nav">
            <Link href="/">Papers</Link>
            <Link href="/categories/">Topics</Link>
            <a href="/rss.xml">RSS</a>
          </nav>
        </div>
      </header>

      <div className="page">{children}</div>

      <footer className="site-footer">
        <div className="footer-inner">
          <p>
            DIPT — automated Software Engineering research monitoring. SERL
            Sweden, Blekinge Institute of Technology. Papers are read and
            summarised by local models; every listed paper passed a
            consistency check.
          </p>
          <button
            type="button"
            className="operator-toggle"
            onClick={toggleOperator}
          >
            Operator mode: {operator ? 'on' : 'off'}
          </button>
        </div>
      </footer>
    </>
  );
}
