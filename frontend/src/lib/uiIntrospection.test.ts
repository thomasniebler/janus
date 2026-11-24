import { afterEach, describe, expect, it } from 'vitest';

import { createIntrospectionSession, elementStyles, parseIntrospectionRequest, STYLE_PROPERTIES } from './uiIntrospection';
import {
  fitSnapshot,
  MAX_SNAPSHOT_BYTES,
  MAX_SNAPSHOT_NODES,
  serializeSnapshot,
  type SnapshotNode,
  type UISnapshot,
} from './uiOutline';

const mount = (html: string) => {
  document.body.innerHTML = html;
};

const byName = (nodes: SnapshotNode[], name: string) => nodes.find((node) => node.name === name);

afterEach(() => {
  document.body.innerHTML = '';
  document.documentElement.className = '';
  document.title = '';
});

describe('parseIntrospectionRequest', () => {
  it('accepts the two request kinds and keeps only string params', () => {
    expect(parseIntrospectionRequest('{"request_id":"r1","kind":"snapshot","params":{}}')).toEqual({
      request_id: 'r1',
      kind: 'snapshot',
      params: {},
    });
    expect(
      parseIntrospectionRequest('{"request_id":"r2","kind":"element_styles","params":{"ref":"e3","n":1}}')
    ).toEqual({ request_id: 'r2', kind: 'element_styles', params: { ref: 'e3' } });
  });

  it('rejects malformed frames', () => {
    expect(parseIntrospectionRequest('not json')).toBeNull();
    expect(parseIntrospectionRequest('{"request_id":"r1","kind":"evaluate"}')).toBeNull();
    expect(parseIntrospectionRequest('{"kind":"snapshot"}')).toBeNull();
    expect(parseIntrospectionRequest('null')).toBeNull();
  });
});

describe('serializeSnapshot', () => {
  it('extracts landmarks, headings, controls, links, images, and text with refs', () => {
    document.title = 'Janus';
    document.documentElement.className = 'dark ui-v2';
    mount(`
      <header><nav aria-label="Primary"><a href="/emails?token=abc#x">Emails</a></nav></header>
      <main>
        <h1>Settings</h1>
        <div role="alert">Could not save</div>
        <p>Pick a <b>colour</b> scheme</p>
        <label for="name">Display name</label>
        <input id="name" type="text" required aria-invalid="true" />
        <label>Theme <select><option>Dark</option><option>Light</option></select></label>
        <button disabled>Save</button>
        <input type="submit" value="Send" />
        <a href="https://example.com/path?q=1">External</a>
        <img src="/x.png" alt="Logo" />
      </main>
    `);
    const { snapshot, refs } = serializeSnapshot();
    const { nodes } = snapshot;
    expect(snapshot.title).toBe('Janus');
    expect(snapshot.viewport).toMatchObject({ color_scheme: 'dark', ui_shell: 'v2', width: window.innerWidth });
    expect(nodes.map((node) => node.ref)).toEqual(nodes.map((_, index) => `e${index + 1}`));
    expect(refs.size).toBe(nodes.length);
    expect(nodes[0]).toMatchObject({ role: 'banner', depth: 0 });
    expect(byName(nodes, 'Primary')).toMatchObject({ role: 'navigation', depth: 1 });
    expect(byName(nodes, 'Emails')).toMatchObject({ role: 'link', href: '/emails', depth: 2 });
    expect(byName(nodes, 'External')).toMatchObject({ role: 'link', href: 'https://example.com' });
    expect(byName(nodes, 'Settings')).toMatchObject({ role: 'heading', level: 1, depth: 1 });
    expect(byName(nodes, 'Could not save')).toMatchObject({ role: 'alert' });
    expect(nodes.find((node) => node.text === 'Pick a scheme')).toMatchObject({ role: 'text' });
    expect(byName(nodes, 'Display name')).toMatchObject({
      role: 'textbox',
      input_type: 'text',
      required: true,
      invalid: true,
    });
    expect(byName(nodes, 'Theme')).toMatchObject({ role: 'combobox', input_type: 'select' });
    expect(byName(nodes, 'Save')).toMatchObject({ role: 'button', disabled: true });
    expect(byName(nodes, 'Send')).toMatchObject({ role: 'button' });
    expect(byName(nodes, 'Logo')).toMatchObject({ role: 'img' });
    // The <label for> text is the control's name, not a separate text run.
    expect(nodes.filter((node) => node.text?.includes('Display name'))).toEqual([]);
  });

  it('never sends values, passwords, editable content, private subtrees, or the chat panel', () => {
    mount(`
      <main>
        <label for="q">Search</label><input id="q" type="search" value="initial secret" />
        <textarea aria-label="Notes">typed notes</textarea>
        <select aria-label="Account"><option selected>alice@example.com</option></select>
        <input type="checkbox" aria-label="Remember me" checked />
        <input type="password" aria-label="Password" value="hunter2" />
        <input autocomplete="current-password" aria-label="Current" />
        <input autocomplete="cc-number" aria-label="Card" />
        <input type="hidden" name="csrf" value="token" />
        <div contenteditable="true" aria-label="Draft">draft body text</div>
        <section data-janus-private><h2>Private heading</h2><p>private text</p></section>
        <div data-janus-chat><p>chat transcript text</p><button>Send message</button></div>
        <p>visible text</p>
      </main>
    `);
    (document.querySelector('#q') as HTMLInputElement).value = 'what the user typed';
    const { snapshot } = serializeSnapshot();
    const json = JSON.stringify(snapshot);
    for (const secret of [
      'initial secret',
      'what the user typed',
      'typed notes',
      'alice@example.com',
      'hunter2',
      'Password',
      'Current',
      'Card',
      'token',
      'draft body text',
      'Private heading',
      'private text',
      'chat transcript text',
      'Send message',
    ]) {
      expect(json).not.toContain(secret);
    }
    expect(json).not.toContain('"checked"');
    expect(byName(snapshot.nodes, 'Search')).toMatchObject({ role: 'searchbox', input_type: 'search' });
    expect(byName(snapshot.nodes, 'Notes')).toMatchObject({ role: 'textbox', input_type: 'textarea' });
    expect(byName(snapshot.nodes, 'Remember me')).toMatchObject({ role: 'checkbox', input_type: 'checkbox' });
    expect(byName(snapshot.nodes, 'Draft')).toMatchObject({ role: 'textbox', input_type: 'contenteditable' });
    expect(snapshot.skipped_private).toBe(1);
    expect(snapshot.nodes.some((node) => node.text === 'visible text')).toBe(true);
  });

  it('skips hidden elements', () => {
    mount(`
      <main>
        <p hidden>hidden attr</p>
        <p aria-hidden="true">aria hidden</p>
        <p style="display: none">display none</p>
        <div style="visibility: hidden"><button>Invisible</button></div>
        <p>shown</p>
      </main>
    `);
    const json = JSON.stringify(serializeSnapshot().snapshot);
    for (const text of ['hidden attr', 'aria hidden', 'display none', 'Invisible']) expect(json).not.toContain(text);
    expect(json).toContain('shown');
  });

  it('leaves hidden descendants out of names and labels', () => {
    mount(`
      <main>
        <button><span hidden>secret one</span>Visible button</button>
        <a href="/emails"><span aria-hidden="true">secret two</span>Visible link</a>
        <h2><span style="display: none">secret three</span>Visible heading</h2>
        <div role="alert"><span style="visibility: hidden">secret four</span>Visible alert</div>
        <label for="field"><span hidden>secret five</span>Visible label</label><input id="field" />
        <span id="lbl"><span hidden>secret six</span>Labelled by</span><button aria-labelledby="lbl"></button>
        <button title="Fallback title"><img hidden alt="secret seven" /></button>
      </main>
    `);
    const { nodes } = serializeSnapshot().snapshot;
    const json = JSON.stringify(nodes);
    for (const secret of ['one', 'two', 'three', 'four', 'five', 'six', 'seven']) {
      expect(json).not.toContain(`secret ${secret}`);
    }
    expect(byName(nodes, 'Visible button')).toMatchObject({ role: 'button' });
    expect(byName(nodes, 'Visible link')).toMatchObject({ role: 'link' });
    expect(byName(nodes, 'Visible heading')).toMatchObject({ role: 'heading' });
    expect(byName(nodes, 'Visible alert')).toMatchObject({ role: 'alert' });
    expect(byName(nodes, 'Visible label')).toMatchObject({ role: 'textbox' });
    expect(byName(nodes, 'Labelled by')).toMatchObject({ role: 'button' });
    expect(byName(nodes, 'Fallback title')).toMatchObject({ role: 'button' });
  });

  it('leaves out inputs whose autocomplete has a secret token after a section or address prefix', () => {
    mount(`
      <main>
        <input autocomplete="billing cc-number" aria-label="Billing card" />
        <input autocomplete="section-checkout one-time-code" aria-label="Checkout code" />
        <input autocomplete="shipping new-password" aria-label="Shipping password" />
        <input autocomplete="  Section-a  CC-CSC " aria-label="Upper case csc" />
        <input autocomplete="shipping street-address" aria-label="Street" />
      </main>
    `);
    const json = JSON.stringify(serializeSnapshot().snapshot);
    for (const name of ['Billing card', 'Checkout code', 'Shipping password', 'Upper case csc']) {
      expect(json).not.toContain(name);
    }
    expect(json).toContain('Street');
  });

  it('caps the node count and text length', () => {
    const long = 'x'.repeat(500);
    mount(`<main>${Array.from({ length: 260 }, (_, index) => `<p>${index} ${long}</p>`).join('')}</main>`);
    const { snapshot, refs } = serializeSnapshot();
    expect(snapshot.nodes.length).toBeLessThanOrEqual(MAX_SNAPSHOT_NODES);
    expect(new TextEncoder().encode(JSON.stringify(snapshot)).length).toBeLessThanOrEqual(MAX_SNAPSHOT_BYTES);
    expect(snapshot.omitted_nodes).toBe(261 - snapshot.nodes.length);
    expect(Math.max(...snapshot.nodes.map((node) => (node.text ?? '').length))).toBeLessThanOrEqual(160);
    expect(refs.size).toBe(snapshot.nodes.length);
  });
});

describe('fitSnapshot', () => {
  it('drops nodes from the end until the JSON fits', () => {
    const snapshot: UISnapshot = {
      route: '/',
      title: '',
      viewport: {
        width: 1,
        height: 1,
        scroll_x: 0,
        scroll_y: 0,
        device_pixel_ratio: 1,
        color_scheme: 'light',
        ui_shell: 'v1',
      },
      nodes: Array.from({ length: 50 }, (_, index) => ({ ref: `e${index + 1}`, role: 'text', depth: 0, text: 'y'.repeat(50) })),
      omitted_nodes: 2,
    };
    const fitted = fitSnapshot(snapshot, 1000);
    expect(new TextEncoder().encode(JSON.stringify(fitted)).length).toBeLessThanOrEqual(1000);
    expect(fitted.nodes[0].ref).toBe('e1');
    expect(fitted.omitted_nodes).toBe(2 + 50 - fitted.nodes.length);
    expect(fitSnapshot(snapshot)).toBe(snapshot);
  });
});

describe('elementStyles', () => {
  it('returns only allowlisted properties and no url() values', () => {
    mount('<button id="b" style="opacity: 0.5; display: inline-block; background-image: url(/leak.png); color: red">Go</button>');
    const element = document.getElementById('b')!;
    const result = elementStyles(element, 'e1');
    expect(result.ref).toBe('e1');
    expect(result.tag).toBe('button');
    expect(Object.keys(result.box)).toEqual(['x', 'y', 'width', 'height']);
    expect(result.styles.opacity).toBe('0.5');
    expect(result.styles.display).toBe('inline-block');
    expect(Object.keys(result.styles).every((key) => (STYLE_PROPERTIES as readonly string[]).includes(key))).toBe(true);
    expect(JSON.stringify(result)).not.toContain('url(');
    expect(result.styles).not.toHaveProperty('background-image');
  });
});

describe('createIntrospectionSession', () => {
  it('answers styles only for refs of the latest snapshot that are still on the page', () => {
    mount('<main><button id="b">Save</button></main>');
    const session = createIntrospectionSession();
    expect(session.answer({ request_id: 'r0', kind: 'element_styles', params: { ref: 'e1' } })).toMatchObject({
      ok: false,
      error: expect.stringContaining('unknown ref e1'),
    });
    const snapshot = session.answer({ request_id: 'r1', kind: 'snapshot', params: {} });
    expect(snapshot.ok).toBe(true);
    const nodes = (snapshot.result as UISnapshot).nodes;
    const save = byName(nodes, 'Save')!;
    const styles = session.answer({ request_id: 'r2', kind: 'element_styles', params: { ref: save.ref } });
    expect(styles).toMatchObject({ ok: true, result: { ref: save.ref, tag: 'button' } });

    document.getElementById('b')!.remove();
    expect(session.answer({ request_id: 'r3', kind: 'element_styles', params: { ref: save.ref } })).toMatchObject({
      ok: false,
      error: expect.stringContaining('no longer on the page'),
    });
  });
});
