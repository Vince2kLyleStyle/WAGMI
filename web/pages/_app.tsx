import '../styles/globals.css';
import type { AppProps } from 'next/app';
import type { NextPage } from 'next';
import Head from 'next/head';
import { useRouter } from 'next/router';
import { AnimatePresence, motion } from 'framer-motion';
import ErrorBoundary from '../components/ErrorBoundary';
import Layout from '../components/Layout';

// Pages can set `noLayout = true` to skip the sidebar layout (e.g. landing page)
type NextPageWithLayout = NextPage & { noLayout?: boolean };
type AppPropsWithLayout = AppProps & { Component: NextPageWithLayout };

export default function App({ Component, pageProps }: AppPropsWithLayout) {
  const router = useRouter();

  // Landing page (/) skips the sidebar layout
  const noLayout = Component.noLayout || router.pathname === '/';

  const content = (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={router.pathname}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -4 }}
        transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
      >
        <Component {...pageProps} />
      </motion.div>
    </AnimatePresence>
  );

  return (
    <>
      <Head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>WAGMI — AI-Powered Perpetual Trading</title>
        <meta name="description" content="AI-powered perpetual trading on Hyperliquid. Multi-strategy signals, 9-agent AI brain, real-time performance." />
      </Head>
      <ErrorBoundary>
        {noLayout ? content : <Layout>{content}</Layout>}
      </ErrorBoundary>
    </>
  );
}
