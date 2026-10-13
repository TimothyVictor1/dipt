import Link from 'next/link';

/**
 * A trail of links ending in the current page (plain text, not a link).
 * `items`: [{ label, href }]; the last item's href is ignored.
 */
export default function Breadcrumb({ items }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      {items.map((it, i) => {
        const last = i === items.length - 1;
        return (
          <span key={i}>
            {last || !it.href ? (
              <span className="crumb-current">{it.label}</span>
            ) : (
              <Link href={it.href}>{it.label}</Link>
            )}
            {!last ? <span className="crumb-sep">/</span> : null}
          </span>
        );
      })}
    </nav>
  );
}
