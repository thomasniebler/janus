import { describe, expect, it } from 'vitest';
import { linkWikiPages, MAX_WIKI_TARGET_LENGTH, wikiPageHref } from './wikiLinks';

describe('linkWikiPages', () => {
  it('links a page title to the Wiki tab', () => {
    expect(linkWikiPages('See [[Acme renewal]].')).toBe('See [Acme renewal](/memory?tab=wiki&wiki=Acme%20renewal).');
  });

  it('uses the text after a pipe as the label', () => {
    expect(linkWikiPages('[[Dana Whitfield|Dana]] leads it.')).toBe(
      '[Dana](/memory?tab=wiki&wiki=Dana%20Whitfield) leads it.',
    );
    expect(linkWikiPages('[[Dana Whitfield| ]]')).toBe('[Dana Whitfield](/memory?tab=wiki&wiki=Dana%20Whitfield)');
  });

  it('links every reference on a line and across lines', () => {
    expect(linkWikiPages('[[A]] and [[B]]\n- [[C]]')).toBe(
      '[A](/memory?tab=wiki&wiki=A) and [B](/memory?tab=wiki&wiki=B)\n- [C](/memory?tab=wiki&wiki=C)',
    );
  });

  it('leaves inline code spans alone', () => {
    expect(linkWikiPages('Write `[[Title]]` to link, like [[Title]].')).toBe(
      'Write `[[Title]]` to link, like [Title](/memory?tab=wiki&wiki=Title).',
    );
    expect(linkWikiPages('``a ` [[X]]`` [[Y]]')).toBe('``a ` [[X]]`` [Y](/memory?tab=wiki&wiki=Y)');
    // An unmatched backtick is just text.
    expect(linkWikiPages('a ` [[X]]')).toBe('a ` [X](/memory?tab=wiki&wiki=X)');
  });

  it('leaves fenced code blocks alone', () => {
    const markdown = '[[Before]]\n```md\n[[Inside]]\n```\n~~~\n[[Tilde]]\n~~~\n[[After]]';
    expect(linkWikiPages(markdown)).toBe(
      '[Before](/memory?tab=wiki&wiki=Before)\n```md\n[[Inside]]\n```\n~~~\n[[Tilde]]\n~~~\n[After](/memory?tab=wiki&wiki=After)',
    );
    // An unclosed fence (e.g. mid-stream) runs to the end.
    expect(linkWikiPages('```\n[[Open]]')).toBe('```\n[[Open]]');
  });

  it('leaves fenced code inside blockquotes and lists alone', () => {
    const quoted = '> ```\n> [[Quoted]]\n> ```\n[[After]]';
    expect(linkWikiPages(quoted)).toBe('> ```\n> [[Quoted]]\n> ```\n[After](/memory?tab=wiki&wiki=After)');
    const listed = '- ```\n  [[Listed]]\n  ```\n1. ~~~\n   [[Numbered]]\n   ~~~';
    expect(linkWikiPages(listed)).toBe(listed);
    // A list item that only mentions a page is still linked.
    expect(linkWikiPages('- see [[Page]]')).toBe('- see [Page](/memory?tab=wiki&wiki=Page)');
  });

  it('keeps numeric markers and wiki citation links intact', () => {
    expect(linkWikiPages('Due in Q4 [[3]].')).toBe('Due in Q4 [[3]].');
    expect(linkWikiPages('Approved [[2]](#wiki-cite-2) by [[Dana]].')).toBe(
      'Approved [[2]](#wiki-cite-2) by [Dana](/memory?tab=wiki&wiki=Dana).',
    );
  });

  it('leaves embeds, escaped brackets, empty and over-long targets alone', () => {
    expect(linkWikiPages('![[diagram.png]] \\[[Not a link]] [[ ]] [[|label]]')).toBe(
      '![[diagram.png]] \\[[Not a link]] [[ ]] [[|label]]',
    );
    const long = 'x'.repeat(MAX_WIKI_TARGET_LENGTH + 1);
    expect(linkWikiPages(`[[${long}]]`)).toBe(`[[${long}]]`);
    const longest = 'x'.repeat(MAX_WIKI_TARGET_LENGTH);
    expect(linkWikiPages(`[[${longest}]]`)).toBe(`[${longest}](/memory?tab=wiki&wiki=${longest})`);
  });

  it('escapes the label so it renders as text only', () => {
    expect(linkWikiPages('[[Page|*bold* <b>hi</b> &amp; _x_]]')).toBe(
      '[\\*bold\\* \\<b\\>hi\\<\\/b\\> \\&amp\\; \\_x\\_](/memory?tab=wiki&wiki=Page)',
    );
    // Brackets cannot appear inside [[...]], and a code span wins over the link, as in CommonMark.
    expect(linkWikiPages('[[Page|a [b] c]]')).toBe('[[Page|a [b] c]]');
    expect(linkWikiPages('[[Page|`c`]]')).toBe('[[Page|`c`]]');
  });

  it('encodes the target so it stays one Markdown destination', () => {
    expect(linkWikiPages('[[R&D (2026) / Q4?]]')).toBe(
      '[R\\&D \\(2026\\) \\/ Q4\\?](/memory?tab=wiki&wiki=R%26D%20%282026%29%20%2F%20Q4%3F)',
    );
    expect(new URLSearchParams(wikiPageHref('R&D (2026) / Q4?').split('?')[1]).get('wiki')).toBe('R&D (2026) / Q4?');
  });

  it('returns text without wiki links unchanged', () => {
    const markdown = 'Plain [link](https://example.com) and [1].';
    expect(linkWikiPages(markdown)).toBe(markdown);
  });
});
