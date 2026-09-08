# DIPT public site

A static Next.js (pages router) + Fuse.js site listing every `approved` paper
with its summary, industrial-relevance score, and categories. No server, no
database access at runtime: all data is baked in at build time.

## Data flow

```
PostgreSQL (approved papers)
   -> python -m dipt.site_export
        website/data/papers.json        (full records + parsed summary)
        website/data/categories.json    (category facet with counts)
        website/public/rss.xml          (RSS 2.0 feed)
   -> npm run build
        website/out/                    (static site for Cloudflare Pages)
```

`dipt.site_export` runs automatically at the end of every scheduled pipeline
run (`python -m dipt.scheduler`). You can also run it by hand any time.

## Develop

```
cd website
npm install
npm run dev            # http://localhost:3000, reads data/*.json
```

If `data/papers.json` does not exist yet, run `python -m dipt.site_export`
first (from the project root). It writes an empty-but-valid file when there are
no approved papers, so the build still succeeds.

## Build and deploy

```
cd website
npm ci
npm run build          # -> out/  (output: 'export' in next.config.js)
```

Deploy `out/` to any static host. For Cloudflare Pages:

- Connect the repo, set build command `npm run build`, output directory
  `website/out`, and root directory `website`.
- A commit that changes `website/data/papers.json` triggers a rebuild, so the
  site refreshes whenever the pipeline approves new papers.
- Or deploy straight from the production machine:
  `npx wrangler pages deploy out`.

## Pages

| Route | Source |
|-------|--------|
| `/` | All papers, client-side Fuse.js search, category + min-score filters. |
| `/papers/[id]/` | One paper: four-part summary, score + rationale, abstract. |
| `/categories/` | Every category with an approved paper. |
| `/categories/[slug]/` | Papers in one category, highest score first. |
| `/rss.xml` | Feed of the 50 most recent approved papers. |
