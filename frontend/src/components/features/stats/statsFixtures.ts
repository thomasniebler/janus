import type { StatsSummary } from '../../../types';

/** A deterministic 30-day summary for Storybook (JAN-258). */
export const makeStatsSample = (days = 30): StatsSummary => {
  const end = new Date(2026, 8, 30);
  const dates = Array.from({ length: days }, (_, index) => {
    const day = new Date(end);
    day.setDate(end.getDate() - (days - 1 - index));
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
  });
  const wave = (index: number, base: number, spread: number) => Math.max(0, Math.round(base + spread * Math.sin(index * 0.9)));
  const mail = dates.map((date, index) => ({
    date,
    important: wave(index, 3, 2),
    digest: wave(index + 2, 12, 5),
    ignore: wave(index + 4, 4, 3),
    spam: wave(index + 1, 6, 4),
  }));
  const triggers = dates.map((date, index) => ({
    date,
    completed: wave(index, 5, 2),
    failed: index % 6 === 0 ? 1 : 0,
    cancelled: index % 11 === 0 ? 1 : 0,
    pending: index === days - 1 ? 2 : 0,
  }));
  const messaging = dates.map((date, index) => ({
    date,
    whatsapp_in: wave(index, 8, 6),
    whatsapp_out: wave(index + 3, 4, 3),
    chat_user: wave(index + 1, 3, 3),
    chat_assistant: wave(index + 1, 3, 3),
  }));
  const total = <T extends { date: string }>(rows: T[], key: Exclude<keyof T, 'date'>) =>
    rows.reduce((sum, row) => sum + Number(row[key]), 0);
  return {
    days: 30,
    timezone: 'Europe/Berlin',
    start: `${dates[0]}T00:00:00Z`,
    end: `${dates[days - 1]}T23:59:59Z`,
    dates,
    mail: {
      per_day: mail,
      totals: { important: total(mail, 'important'), digest: total(mail, 'digest'), ignore: total(mail, 'ignore'), spam: total(mail, 'spam') },
      total: mail.reduce((sum, row) => sum + row.important + row.digest + row.ignore + row.spam, 0),
    },
    triggers: {
      per_day: triggers,
      totals: {
        completed: total(triggers, 'completed'),
        failed: total(triggers, 'failed'),
        cancelled: total(triggers, 'cancelled'),
        pending: total(triggers, 'pending'),
      },
      total: triggers.reduce((sum, row) => sum + row.completed + row.failed + row.cancelled + row.pending, 0),
      success_rate: 0.96,
      definitions: [
        { definition_id: 3, name: 'Mail processing', origin: 'system', runs: 96, completed: 93, failed: 3, cancelled: 0, pending: 0, success_rate: 0.97, avg_duration_seconds: 14.2, last_run_at: '2026-09-30T09:12:00Z' },
        { definition_id: 1, name: 'Morning briefing', origin: 'system', runs: 30, completed: 30, failed: 0, cancelled: 0, pending: 0, success_rate: 1, avg_duration_seconds: 48.5, last_run_at: '2026-09-30T04:30:00Z' },
        { definition_id: 5, name: 'Follow-up: Check whether the vendor replied', origin: 'agent', runs: 1, completed: 0, failed: 0, cancelled: 0, pending: 1, success_rate: null, avg_duration_seconds: null, last_run_at: '2026-09-30T13:00:00Z' },
        { definition_id: null, name: null, origin: null, runs: 4, completed: 3, failed: 1, cancelled: 0, pending: 0, success_rate: 0.75, avg_duration_seconds: 22, last_run_at: '2026-09-21T10:00:00Z' },
      ],
    },
    memory: {
      per_day: dates.map((date, index) => ({ date, new_facts: index % 3 === 0 ? 2 : index % 4 === 0 ? 1 : 0 })),
      new_facts: 17,
      active_facts: 142,
      archived_facts: 23,
      active_topics: 8,
      total_topics: 31,
      wiki_pages: 12,
      knowledge_sources: 486,
    },
    messaging: {
      per_day: messaging,
      totals: {
        whatsapp_in: total(messaging, 'whatsapp_in'),
        whatsapp_out: total(messaging, 'whatsapp_out'),
        chat_user: total(messaging, 'chat_user'),
        chat_assistant: total(messaging, 'chat_assistant'),
      },
    },
    approvals: {
      automations: {
        totals: { proposed: 1, approved: 4, rejected: 1, withdrawn: 0, superseded: 0 },
        total: 6,
        approval_rate: 0.8,
        median_hours_to_decision: 3.5,
      },
      chat_tools: {
        totals: { approved: 9, denied: 1, timeout: 2, disconnected: 0, unavailable: 0 },
        total: 12,
        approval_rate: 0.9,
        median_hours_to_decision: null,
      },
      character: { totals: { proposed: 0, approved: 1, rejected: 1 }, total: 2, approval_rate: 0.5, median_hours_to_decision: 12 },
    },
  };
};
