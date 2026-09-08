import { useEffect, useState } from 'react';

/**
 * "Share this paper" panel. Shows the AI-drafted post (editable), and one-click
 * buttons that open the LinkedIn / X composer pre-filled, plus a copy button
 * for anywhere else. The link always points at this paper's page on the site.
 */
export default function ShareBox({ post, title }) {
  const [text, setText] = useState(post || '');
  const [url, setUrl] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setUrl(window.location.href);
  }, []);

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
      <h2>Share this paper</h2>
      <p className="muted share-hint">
        {post
          ? 'A draft post is ready. Edit it if you like, then post.'
          : 'No draft was generated for this paper. Write a line and share it.'}
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
        <button className="btn btn-ghost" type="button" onClick={copy}>
          {copied ? 'Copied' : 'Copy post + link'}
        </button>
      </div>
    </section>
  );
}
