import { useEffect, useState } from 'react';

/**
 * "Share this paper" panel — for the site owner only.
 *
 * Renders nothing unless operator mode is on (a flag the owner sets from the
 * footer, stored in localStorage). A visitor who arrives from a shared
 * LinkedIn / X post never sees it, so there is no re-share loop and no social
 * controls on the public read page.
 */
export default function ShareBox({ post, title }) {
  const [operator, setOperator] = useState(false);
  const [text, setText] = useState(post || '');
  const [url, setUrl] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    try {
      setOperator(localStorage.getItem('dipt-operator') === '1');
    } catch {
      setOperator(false);
    }
    setUrl(window.location.href);
  }, []);

  if (!operator) return null;

  const body = (text || title || '').trim();
  const withLink = url ? `${body}\n\n${url}` : body;

  const xHref =
    'https://twitter.com/intent/tweet?text=' +
    encodeURIComponent(body) +
    (url ? '&url=' + encodeURIComponent(url) : '');
  const liHref =
    'https://www.linkedin.com/feed/?shareActive=true&text=' +
    encodeURIComponent(withLink);

  async function copy() {
    try {
      await navigator.clipboard.writeText(withLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="share">
      <span className="share-flag">Operator</span>
      <h3>Share this paper</h3>
      <p className="share-hint">
        {post
          ? 'A draft post is ready. Edit it if you like, then post. The link points to this page.'
          : 'No draft was generated. Write a line and share it — the link points to this page.'}
      </p>
      <textarea
        className="share-text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={5}
        placeholder="Write a short post to share this paper…"
      />
      <div className="share-actions">
        <a className="btn" href={xHref} target="_blank" rel="noreferrer">
          Share on X
        </a>
        <a className="btn" href={liHref} target="_blank" rel="noreferrer">
          Share on LinkedIn
        </a>
        <button className="btn btn-primary" type="button" onClick={copy}>
          {copied ? 'Copied' : 'Copy post + link'}
        </button>
      </div>
    </section>
  );
}
