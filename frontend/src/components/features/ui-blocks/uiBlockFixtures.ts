/** Valid catalog blocks (JAN-252) as the backend streams and stores them, shared by stories and tests. */

export const cardBlock = {
  id: 'blk_card00000001',
  version: 1,
  type: 'card',
  title: 'Invoice overdue',
  subtitle: 'billing@example.com · Friday',
  body: 'Payment of <b>120 EUR</b> was due on Friday.',
  fields: [
    { label: 'Amount', value: '120 EUR' },
    { label: 'Account', value: 'Work' },
  ],
  links: [
    { label: 'Open in Emails', href: '/emails' },
    { label: 'Pay online', href: 'https://pay.example.com/invoice/42' },
  ],
};

export const tableBlock = {
  id: 'blk_table0000001',
  version: 1,
  type: 'table',
  title: 'Open tasks',
  columns: ['Task', 'Due', 'Done'],
  rows: [
    ['Pay invoice', 'Fri', false],
    ['Call Anna', null, true],
    ['Book flights', 'Mon', false],
  ],
};

export const barChartBlock = {
  id: 'blk_chart0000001',
  version: 1,
  type: 'chart',
  title: 'Mail this week',
  kind: 'bar',
  labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
  series: [
    { name: 'Important', values: [3, 5, 2, 4, 1] },
    { name: 'Digest', values: [8, 6, 9, 7, 5] },
  ],
  unit: 'mails',
};

export const lineChartBlock = {
  ...barChartBlock,
  id: 'blk_chart0000002',
  kind: 'line',
  title: 'Unread over time',
  series: [{ name: 'Unread', values: [12, 9, 14, 6, 4] }],
};

export const pieChartBlock = {
  id: 'blk_chart0000003',
  version: 1,
  type: 'chart',
  title: 'Where your time went',
  kind: 'pie',
  labels: ['Meetings', 'Focus', 'Mail', 'Breaks'],
  series: [{ name: 'Hours', values: [12, 18, 6, 4] }],
  unit: 'h',
};

export const checklistBlock = {
  id: 'blk_check0000001',
  version: 1,
  type: 'checklist',
  title: 'Before the trip',
  items: [
    { text: 'Pay invoice', checked: true },
    { text: 'Reply to Anna', checked: false },
  ],
};

export const keyValueBlock = {
  id: 'blk_keyval000001',
  version: 1,
  type: 'key_value',
  title: 'Inbox',
  items: [
    { key: 'Unread', value: 4 },
    { key: 'Last sync', value: '08:12' },
    { key: 'IDLE connected', value: true },
  ],
};

export const actionsBlock = {
  id: 'blk_action000001',
  version: 1,
  type: 'actions',
  buttons: [
    { label: 'Snooze', tool: 'snooze_topic', args: { topic_id: 4 }, style: 'primary' },
    { label: 'Create rule', tool: 'create_rule', args: { pattern: 'billing@' }, style: 'secondary' },
    { label: 'Delete', tool: 'delete_fact', args: { fact_id: 9 }, style: 'danger' },
  ],
};

export const imageBlock = {
  id: 'blk_image0000001',
  version: 1,
  type: 'image',
  title: 'Sketch',
  alt: 'A pencil sketch of a lighthouse',
  url: '/api/images/abc123',
  caption: 'Generated for you',
};

/** Blocks the client must not render: wrong version, unknown type, external image, a `javascript:` link. */
export const invalidBlocks = {
  futureVersion: { ...cardBlock, version: 2 },
  unknownType: { id: 'blk_x', version: 1, type: 'iframe', src: 'https://evil.example.com' },
  externalImage: { ...imageBlock, url: 'https://tracker.example.com/pixel.png' },
  scriptLink: { ...cardBlock, links: [{ label: 'Click', href: 'javascript:alert(1)' }] },
  raggedTable: { ...tableBlock, rows: [['only one cell']] },
};
