'use client';

import { useMemo, type CSSProperties, type ReactNode } from 'react';
import Link from 'next/link';
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  AlertCircle,
  ArrowDown,
  Building2,
  CheckCircle,
  Cloud,
  FileText,
  History,
  MapPin,
  Network,
  RadioTower,
  RefreshCw,
  Rocket,
  Satellite,
  Scale,
  Server,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Store,
  TrendingDown,
  Users,
  type LucideIcon,
} from 'lucide-react';
import { mediaSrc } from '@/lib/api';
import {
  INLINE_TAGS,
  inlineMarkdown,
  parseGenUI,
  stripGenUI,
  type AttrValue,
  type EntityRef,
  type GenNode,
} from '@/lib/genui';
import type { MediaItem, Source } from '@/types';

/**
 * A GenUI reply, rendered from its component markup.
 *
 * `lib/genui.ts` parses the markup; this maps each tag to a component through
 * one registry (`BLOCKS`). Prose — text with `<Cite/>`, `<Entity/>`, `<Link/>`
 * inside it — is rendered as Markdown, one run at a time, so a sentence with a
 * citation in the middle stays a single paragraph.
 *
 * Three rules keep it safe and honest:
 *   - Markup only becomes DOM through the registry. No raw HTML, and attribute
 *     values (including `onClick={() => …}`) are never evaluated.
 *   - An unknown tag renders its children, so a component ChatGPT adds next
 *     month degrades to readable text instead of raw markup.
 *   - Malformed markup falls back to the reply with its tags stripped.
 *
 * The look follows ChatGPT's own rendering of the same components (screenshots
 * of the Starlink and WhatsApp shares, 2026-10-26).
 */

interface Context {
  sources: Source[];
  media: MediaItem[];
  storyId: string;
}

const ICONS: Record<string, LucideIcon> = {
  'alert-circle': AlertCircle,
  'arrow-down': ArrowDown,
  'building-2': Building2,
  'check-circle': CheckCircle,
  cloud: Cloud,
  'file-text': FileText,
  history: History,
  'map-pin': MapPin,
  network: Network,
  'radio-tower': RadioTower,
  'refresh-cw': RefreshCw,
  rocket: Rocket,
  satellite: Satellite,
  scale: Scale,
  server: Server,
  'shield-alert': ShieldAlert,
  'shield-check': ShieldCheck,
  smartphone: Smartphone,
  store: Store,
  'trending-down': TrendingDown,
  users: Users,
};

const ICON_SIZES: Record<string, number> = { sm: 16, md: 18, lg: 20, xl: 22, '2xl': 26 };

type TagNode = Extract<GenNode, { kind: 'tag' }>;

function str(value: AttrValue | undefined): string | undefined {
  return typeof value === 'string' ? value : typeof value === 'number' ? String(value) : undefined;
}

/** GenUI spacing is in steps; one step reads as four pixels. */
function space(value: AttrValue | undefined): string | undefined {
  return typeof value === 'number' ? `${value * 4}px` : undefined;
}

const ALIGN: Record<string, CSSProperties['alignItems']> = {
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
  stretch: 'stretch',
};

const JUSTIFY: Record<string, CSSProperties['justifyContent']> = {
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
  between: 'space-between',
};

const RADII: Record<string, string> = { sm: '6px', md: '8px', lg: '12px', xl: '16px' };

/**
 * Layout attributes as inline style. `align=center` centres the cross axis; in
 * a column (`box`) that also means centred text, in a `row` it only means
 * vertically centred children.
 */
function flexStyle(node: TagNode, column: boolean): CSSProperties {
  const align = str(node.attrs.align);
  const justify = str(node.attrs.justify);
  const size = str(node.attrs.size);
  const fixed = size && /^\d+px$/.test(size) ? size : undefined;
  return {
    gap: space(node.attrs.gap),
    padding: space(node.attrs.padding),
    alignItems: align ? ALIGN[align] : undefined,
    justifyContent: justify ? JUSTIFY[justify] : undefined,
    textAlign: column && align === 'center' ? 'center' : undefined,
    flex: fixed ? 'none' : str(node.attrs.flex),
    minWidth: node.attrs.flex ? 0 : undefined,
    borderRadius: RADII[str(node.attrs.radius) ?? ''],
    width: fixed,
    height: fixed,
  };
}

/** `"3:4"` → `"3 / 4"` for CSS `aspect-ratio`. */
function ratio(value: AttrValue | undefined): string | undefined {
  const text = str(value);
  return text && /^\d+:\d+$/.test(text) ? text.replace(':', ' / ') : undefined;
}

function textOf(nodes: GenNode[]): string {
  return nodes
    .map((node) => (node.kind === 'text' ? node.text : textOf(node.children)))
    .join('')
    .replace(/\s+/g, ' ')
    .trim();
}

function imagesFor(node: TagNode, ctx: Context): MediaItem[] {
  return ctx.media
    .filter((item) => item.tag_start === node.start && item.src)
    .sort((a, b) => a.ordinal - b.ordinal);
}

const MARKER = /^\[\d+\]$/;

function InlineRun({ nodes, ctx }: { nodes: GenNode[]; ctx: Context }) {
  const { markdown, entities } = useMemo(
    () => inlineMarkdown(nodes, ctx.sources),
    [nodes, ctx.sources],
  );
  if (!markdown.trim()) return null;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => (url.startsWith('genui-entity:') ? url : defaultUrlTransform(url))}
      components={inlineComponents(entities)}
    >
      {markdown}
    </ReactMarkdown>
  );
}

function inlineComponents(entities: EntityRef[]): Components {
  return {
    a({ href, children }) {
      if (href?.startsWith('genui-entity:')) {
        const entity = entities[Number(href.slice('genui-entity:'.length))];
        return (
          <span className="gx-entity" title={entity?.disambig || undefined}>
            {children}
          </span>
        );
      }
      // Citation markers stay compact; every other link out gets ChatGPT's ↗.
      const label = typeof children === 'string' ? children : '';
      const marker = MARKER.test(label);
      return (
        <a href={href} target="_blank" rel="noreferrer" className={marker ? undefined : 'gx-link'}>
          {children}
          {marker ? null : <span aria-hidden="true"> ↗</span>}
        </a>
      );
    },
  };
}

/** A container's children: block components as themselves, prose as runs. */
function renderChildren(nodes: GenNode[], ctx: Context): ReactNode[] {
  const out: ReactNode[] = [];
  let run: GenNode[] = [];
  const flush = () => {
    if (run.some((node) => node.kind === 'tag' || node.text.trim())) {
      out.push(<InlineRun key={`run-${run[0].start}`} nodes={run} ctx={ctx} />);
    }
    run = [];
  };
  for (const node of nodes) {
    if (node.kind === 'text' || INLINE_TAGS.has(node.tag)) {
      run.push(node);
    } else {
      flush();
      out.push(<Block key={`${node.tag}-${node.start}`} node={node} ctx={ctx} />);
    }
  }
  flush();
  return out;
}

type BlockRenderer = (node: TagNode, ctx: Context) => ReactNode;

const BLOCKS: Record<string, BlockRenderer> = {
  box(node, ctx) {
    // `border` draws a card; `background` alone a filled tile (the report
    // card's icon square), as ChatGPT draws them.
    const classes = ['gx-box', node.attrs.border ? 'gx-card' : '', node.attrs.background ? 'gx-fill' : ''];
    return (
      <div className={classes.filter(Boolean).join(' ')} style={flexStyle(node, true)}>
        {renderChildren(node.children, ctx)}
      </div>
    );
  },
  row(node, ctx) {
    return (
      <div className="gx-row" style={flexStyle(node, false)}>
        {renderChildren(node.children, ctx)}
      </div>
    );
  },
  grid(node, ctx) {
    const columns = typeof node.attrs.columns === 'number' ? node.attrs.columns : 2;
    return (
      <div
        className="gx-grid"
        style={{ gap: space(node.attrs.gap), gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {renderChildren(node.children, ctx)}
      </div>
    );
  },
  'grid-item'(node, ctx) {
    return <div className="gx-grid-item">{renderChildren(node.children, ctx)}</div>;
  },
  text(node, ctx) {
    const size = str(node.attrs.size);
    const classes = ['gx-text', size === 'sm' ? 'gx-sm' : '', str(node.attrs.color) === 'secondary' ? 'gx-muted' : ''];
    const align = str(node.attrs.textAlign);
    return (
      <div className={classes.filter(Boolean).join(' ')} style={{ textAlign: align as CSSProperties['textAlign'] }}>
        {renderChildren(node.children, ctx)}
      </div>
    );
  },
  title(node, ctx) {
    return (
      <div className={str(node.attrs.size) === 'lg' ? 'gx-title gx-lg' : 'gx-title'}>
        {renderChildren(node.children, ctx)}
      </div>
    );
  },
  icon(node) {
    const Icon = ICONS[str(node.attrs.name) ?? ''];
    if (!Icon) return null;
    const color = str(node.attrs.color);
    return (
      <Icon
        className={`gx-icon${color ? ` gx-${color}` : ''}`}
        size={ICON_SIZES[str(node.attrs.size) ?? 'md'] ?? 18}
        strokeWidth={1.75}
        aria-hidden="true"
      />
    );
  },
  divider() {
    return <hr className="gx-divider" />;
  },
  button(node, ctx) {
    // Its real target is `sandbox:/mnt/data/…`, which exists only inside
    // ChatGPT. The report it downloads is this story's own Report tab.
    return (
      <Link href={`/chat-archives/${ctx.storyId}`} className="gx-button">
        {textOf(node.children) || 'Read the report'}
      </Link>
    );
  },
  AsyncImage(node, ctx) {
    const [image] = imagesFor(node, ctx);
    if (!image?.src) return null;
    return (
      // eslint-disable-next-line @next/next/no-img-element -- static export, no image optimiser
      <img
        className="gx-image"
        src={mediaSrc(image.src)}
        alt={str(node.attrs.query) ?? ''}
        loading="lazy"
        // `maxWidth` is the thumbnail's width beside text in a row; without it
        // the image takes the column.
        style={{
          aspectRatio: ratio(node.attrs.aspectRatio),
          width: str(node.attrs.maxWidth) ?? '100%',
          maxWidth: '100%',
        }}
      />
    );
  },
  AsyncImageGroup(node, ctx) {
    const images = imagesFor(node, ctx);
    if (!images.length) return null;
    const queries = Array.isArray(node.attrs.query) ? node.attrs.query : [];
    const layout = str(node.attrs.layout) === 'carousel' ? 'carousel' : 'bento';
    return (
      <div
        className={`gx-gallery gx-${layout}${layout === 'bento' ? ` gx-bento-${Math.min(images.length, 3)}` : ''}`}
        style={layout === 'carousel' ? ({ '--gx-ratio': ratio(node.attrs.aspectRatio) } as CSSProperties) : undefined}
      >
        {images.map((image, i) => (
          // eslint-disable-next-line @next/next/no-img-element -- static export, no image optimiser
          <img key={image.ordinal} src={mediaSrc(image.src!)} alt={queries[i] ?? ''} loading="lazy" />
        ))}
      </div>
    );
  },
};

function Block({ node, ctx }: { node: TagNode; ctx: Context }) {
  const render = BLOCKS[node.tag];
  // Unknown component: render what it contains rather than what it is.
  return <>{render ? render(node, ctx) : renderChildren(node.children, ctx)}</>;
}

export default function GenUIMessage({
  text,
  sources,
  media,
  storyId,
}: {
  text: string;
  sources: Source[];
  media: MediaItem[];
  storyId: string;
}) {
  const tree = useMemo(() => parseGenUI(text), [text]);
  const ctx = useMemo(() => ({ sources, media, storyId }), [sources, media, storyId]);

  if (!tree) {
    return (
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripGenUI(text)}</ReactMarkdown>
    );
  }
  return <div className="genui">{renderChildren(tree, ctx)}</div>;
}
