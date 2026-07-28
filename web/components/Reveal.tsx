'use client';

import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';

/**
 * Reveal — fades + rises its children into view on scroll. Respects reduced-motion.
 * Wrap landing sections: <Reveal><section>…</section></Reveal>.
 */
export default function Reveal({
  children,
  delay = 0,
  y = 26,
  style,
}: {
  children: React.ReactNode;
  delay?: number;
  y?: number;
  style?: React.CSSProperties;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div style={style}>{children}</div>;
  return (
    <motion.div
      style={style}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '0px 0px -12% 0px' }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
