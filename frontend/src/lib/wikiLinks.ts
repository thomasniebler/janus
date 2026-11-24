/**
 * Wiki cross-references (JAN-331): Janus writes Obsidian-style `[[Page Title]]` and `[[Page Title|label]]`
 * links in wiki pages and chat replies. `linkWikiPages` rewrites them into ordinary Markdown links to the
 * Wiki tab before the text is rendered, so they can be followed.
 */

/** Targets longer than this are left as text: they are prose in brackets, not page titles. */
export const MAX_WIKI_TARGET_LENGTH = 200;

/** The Wiki tab URL that opens the page with this title (or slug). */
export const wikiPageHref = (title: string) => `/memory?tab=wiki&wiki=${encodeURIComponent(title.trim())}`;

// Every ASCII punctuation character can be backslash-escaped in CommonMark, so an escaped label is
// always plain text: no emphasis, code, nested link, image, autolink, HTML, or entity.
const escapeMarkdownText = (text: string) => text.replace(/[!-/:-@[-`{-~]/g, (char) => `\\${char}`);

// encodeURIComponent leaves parentheses alone, and an unbalanced `)` would end the Markdown destination.
const markdownDestination = (title: string) => wikiPageHref(title).replace(/\(/g, '%28').replace(/\)/g, '%29');

const WIKI_LINK = /\[\[([^[\]\n]+)\]\]/g;

const rewriteWikiLink = (match: string, inner: string, offset: number, text: string) => {
  const before = text[offset - 1];
  // `![[embed]]`, an escaped `\[[`, or `[[x]](url)` (an explicit link, e.g. a wiki citation marker).
  if (before === '!' || before === '\\' || text[offset + match.length] === '(') return match;
  const pipe = inner.indexOf('|');
  const target = (pipe === -1 ? inner : inner.slice(0, pipe)).trim();
  const label = pipe === -1 ? target : inner.slice(pipe + 1).trim() || target;
  if (!target || /^\d+$/.test(target) || target.length > MAX_WIKI_TARGET_LENGTH) return match;
  return `[${escapeMarkdownText(label)}](${markdownDestination(target)})`;
};

const linkProse = (text: string) => text.replace(WIKI_LINK, rewriteWikiLink);

/** Rewrites links in text that holds no fenced code; inline code spans are copied unchanged. */
const linkOutsideCodeSpans = (text: string) => {
  let output = '';
  let prose = '';
  let index = 0;
  while (index < text.length) {
    if (text[index] !== '`') {
      prose += text[index];
      index += 1;
      continue;
    }
    let length = 1;
    while (text[index + length] === '`') length += 1;
    const ticks = '`'.repeat(length);
    const close = new RegExp(`(?<!\`)${ticks}(?!\`)`, 'g');
    close.lastIndex = index + ticks.length;
    const end = close.exec(text);
    if (!end) {
      prose += ticks;
      index += ticks.length;
      continue;
    }
    output += linkProse(prose) + text.slice(index, end.index + ticks.length);
    prose = '';
    index = end.index + ticks.length;
  }
  return output + linkProse(prose);
};

// Blockquote (`>`) and list (`-`, `*`, `+`, `1.`, `1)`) markers in front of a fence: fences nest in them.
const CONTAINER_PREFIX = String.raw`(?:[ \t]*(?:>|[-*+]|\d{1,9}[.)]))*[ \t]*`;
const FENCE_OPEN = new RegExp(`^${CONTAINER_PREFIX}(\`{3,}|~{3,})`);

/** Splits Markdown into alternating prose and fenced-code chunks (fenced chunks keep their fences). */
const splitFences = (markdown: string) => {
  const chunks: { code: boolean; text: string }[] = [];
  let fence: string | null = null;
  let current: string[] = [];
  const flush = (code: boolean) => {
    if (current.length) chunks.push({ code, text: current.join('\n') });
    current = [];
  };
  for (const line of markdown.split('\n')) {
    if (fence === null) {
      const open = FENCE_OPEN.exec(line);
      if (open && !(open[1][0] === '`' && line.slice(open[0].length).includes('`'))) {
        flush(false);
        fence = open[1];
      }
      current.push(line);
    } else {
      current.push(line);
      if (new RegExp(`^${CONTAINER_PREFIX}${fence[0] === '`' ? '`' : '~'}{${fence.length},}\\s*$`).test(line)) {
        flush(true);
        fence = null;
      }
    }
  }
  flush(fence !== null);
  return chunks;
};

/**
 * Rewrites `[[Title]]` and `[[Title|label]]` into Markdown links to the wiki page. Code spans, fenced code,
 * numeric targets (`[[3]]`, the wiki's citation markers), and targets over 200 characters stay as written.
 */
export const linkWikiPages = (markdown: string) => {
  if (!markdown.includes('[[')) return markdown;
  return splitFences(markdown)
    .map((chunk) => (chunk.code ? chunk.text : linkOutsideCodeSpans(chunk.text)))
    .join('\n');
};
