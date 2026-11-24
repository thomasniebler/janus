import { execFile } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { E2E_ADMIN_USERNAME, E2E_BACKEND_ENV } from './env';
import { test } from './fixtures';
import { createThrowawayUser, type E2EUser, type Session } from './helpers';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * Runs a Python snippet against the e2e backend's database and returns what it prints.
 * For state no UI or API can create without a live model or real mail, such as a finished
 * trigger run, a delegated task, mail or a topic mention (JAN-224): the snippet calls the
 * real models and services, so the rows are exactly what the backend would write itself.
 *
 * The snippet gets `db` (a session), `user` (the user named by `as`, the e2e admin unless
 * given; pass a throwaway user's name for a test that runs as one), and `args` (the extra
 * arguments, as strings); the session is closed afterwards.
 *
 * Asynchronous, so a fake upstream served by the test process (`fake-upstream.ts`) can
 * answer the backend code the snippet runs. Each call starts a fresh `uv run python`
 * (seconds on a loaded machine), so the time it takes is added to the running test's
 * timeout: a spec that seeds a few rows is not failed for the interpreter start-ups it
 * cannot avoid.
 */
export const runBackendPython = async (
  snippet: string,
  args: (string | number)[] = [],
  { as = E2E_ADMIN_USERNAME }: { as?: string } = {},
): Promise<string> => {
  const script = [
    'import sys',
    'from janus import database, models',
    'db = database.SessionLocal()',
    'try:',
    '    user = db.query(models.User).filter(models.User.username == sys.argv[1]).one()',
    '    args = sys.argv[2:]',
    ...snippet
      .replace(/^\n+|\s+$/g, '')
      .split('\n')
      .map((line) => `    ${line}`),
    'finally:',
    '    db.close()',
  ].join('\n');
  const started = Date.now();
  const { stdout } = await promisify(execFile)('uv', ['run', '--quiet', 'python', '-c', script, as, ...args.map(String)], {
    cwd: REPO_ROOT,
    env: { ...process.env, ...E2E_BACKEND_ENV },
    encoding: 'utf-8',
    maxBuffer: 16 * 1024 * 1024,
  });
  const info = test.info();
  if (info.timeout > 0) info.setTimeout(info.timeout + (Date.now() - started));
  return stdout;
};

/** The last line a snippet printed, parsed as JSON. */
export const lastJsonLine = <T>(output: string): T => JSON.parse(output.trim().split('\n').pop()!) as T;

export interface SeededMail {
  /** The throwaway user who owns the seeded mail; sign in as them with `signInAs`. */
  user: E2EUser;
  accountId: number;
  emails: Record<'board' | 'newsletter' | 'prize' | 'lunch', number>;
  clarifications: Record<'board' | 'prize', number>;
}

/**
 * Creates a throwaway user (through `admin`) and seeds, owned by them, an inactive mail
 * account, four classified emails (important, two digest, spam) and two pending
 * clarification items carrying the rule suggestion `EmailService` derives from a correction
 * (JAN-219). Nothing lands on the shared e2e admin, so no other spec ever sees this mail,
 * and it needs no cleanup.
 */
export const seedMail = async (admin: Session): Promise<SeededMail> => {
  const user = await createThrowawayUser(admin, 'e2e-mail');
  const output = await runBackendPython(
    `
import json
from datetime import timedelta
# Importing email_service on its own hits a circular import; processor loads it cleanly.
from janus import processor  # noqa: F401
from janus.services.email_service import EmailService

# Inactive, so no IMAP monitor ever tries to reach this made-up server.
account = models.EmailAccount(
    user_id=user.id, name="E2E seeded inbox", email="me@e2e.example", imap_server="imap.e2e.invalid",
    username="me@e2e.example", password="unused", is_active=False,
)
db.add(account)
db.flush()
now = models.utcnow()
P, C = models.Priority, models.ConfidenceLevel
rows = {
    "board": ("ceo@e2e.example", "E2E Board meeting moved to Monday", P.IMPORTANT, C.HIGH,
              "The CEO asks you to prepare slides.", 1),
    "newsletter": ("news@e2e.example", "E2E Weekly newsletter", P.DIGEST, C.MEDIUM,
                   "A recurring newsletter.", 2),
    "prize": ("prize@e2e-spam.example", "E2E You won a prize", P.SPAM, C.LOW,
              "Unsolicited prize notification.", 3),
    "lunch": ("colleague@e2e.example", "E2E Lunch on Friday", P.DIGEST, C.MEDIUM,
              "Casual lunch plans.", 4),
}
emails = {}
for key, (sender, subject, priority, confidence, reasoning, hours_ago) in rows.items():
    record = models.EmailRecord(
        account_id=account.id, message_id=f"<{key}-{account.id}@e2e.example>", sender=sender, subject=subject,
        snippet=f"Body of {subject}.", priority=priority, classification_confidence=confidence,
        reasoning=reasoning, received_at=now - timedelta(hours=hours_ago),
    )
    db.add(record)
    db.flush()
    emails[key] = record
# No live code path queues a clarification any more (a correction becomes an active
# rule right away, JAN-74), so the pending items are written directly, with the
# suggestion EmailService itself would derive from the correction.
service = EmailService(db)
clarification_rows = {}
for key, previous, requested in (("board", P.IMPORTANT, P.DIGEST), ("prize", P.SPAM, P.IGNORE)):
    clarification = models.EmailClarificationRequest(
        user_id=user.id, email_id=emails[key].id, previous_priority=previous, requested_priority=requested,
        **service._build_rule_suggestion(emails[key], requested),
    )
    db.add(clarification)
    clarification_rows[key] = clarification
db.commit()
print(json.dumps({
    "accountId": account.id,
    "emails": {key: record.id for key, record in emails.items()},
    "clarifications": {key: row.id for key, row in clarification_rows.items()},
}))
`,
    [],
    { as: user.username },
  );
  return { user, ...lastJsonLine<Omit<SeededMail, 'user'>>(output) };
};
