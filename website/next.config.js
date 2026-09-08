/**
 * Static export configuration.
 *
 * `output: 'export'` makes `next build` emit a fully static site into `out/`,
 * which is what Cloudflare Pages serves. There is no Node server in production.
 * `trailingSlash` keeps clean directory-style URLs on static hosts.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};

module.exports = nextConfig;
