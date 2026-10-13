import { Inter } from 'next/font/google';
import '@/styles/globals.css';

/*
 * Inter is the OpenAlex UI typeface. next/font downloads it at build time and
 * self-hosts the files in the static export — no runtime request to Google,
 * so it stays GDPR-clean. `variable` exposes it as --font-inter, which
 * globals.css folds into --ox-font-sans.
 */
const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
});

export default function App({ Component, pageProps }) {
  return (
    <div className={inter.variable}>
      <Component {...pageProps} />
    </div>
  );
}
