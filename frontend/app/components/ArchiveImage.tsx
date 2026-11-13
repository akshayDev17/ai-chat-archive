'use client';

import { useState, type ImgHTMLAttributes } from 'react';
import { imageRetrySrc } from '@/lib/api';

/**
 * A copied image. One on the signed-in route is asked for a second time if it
 * fails to load — once: if that fails too, it stays the broken image it is.
 */
export default function ArchiveImage({
  src,
  alt,
  ...rest
}: ImgHTMLAttributes<HTMLImageElement> & { src: string; alt: string }) {
  const [failed, setFailed] = useState<string | null>(null);
  const retry = failed === src ? imageRetrySrc(src) : null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static export, no image optimiser
    <img {...rest} alt={alt} src={retry ?? src} onError={() => setFailed(src)} />
  );
}
