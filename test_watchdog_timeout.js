// Host-static tests for the Panel.qml process-watchdog deadline (PR #8 follow-up).
// The QML function is plain JS, so extract it from the source and evaluate it with
// a mocked `root`/`askProc` — no Qt runtime needed.
// Run: node test_watchdog_timeout.js
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync(__dirname + '/Panel.qml', 'utf8');
const start = src.indexOf('function procTimeoutMsFor(proc)');
if (start < 0) { console.error('procTimeoutMsFor not found'); process.exit(1); }
const rest = src.slice(start);
const end = rest.indexOf('\n  }');
if (end < 0) { console.error('procTimeoutMsFor end not found'); process.exit(1); }
const fnText = rest.slice(0, end + 4);

function makeFn(rawStatus, procTimeoutMs) {
  const askProc = { id: 'askProc' };
  const root = { rawStatus: rawStatus, procTimeoutMs: procTimeoutMs };
  const sandbox = { root: root, askProc: askProc, Math: Math, Number: Number };
  vm.createContext(sandbox);
  vm.runInContext(fnText + '\nthis.fn = procTimeoutMsFor;', sandbox);
  return { fn: sandbox.fn, askProc: askProc };
}

let failures = 0;
function check(name, got, expected) {
  const ok = got === expected;
  if (!ok) failures++;
  console.log((ok ? 'ok  ' : 'FAIL') + ' ' + name + ' -> ' + got + ' (expected ' + expected + ')');
}
function deadlines(status, base) {
  const m = makeFn(status, base);
  return { ask: m.fn(m.askProc), helper: m.fn({ id: 'statusProc' }) };
}

let c = deadlines({ request_timeout_seconds: 600 }, 300000);
check('helper keeps 300s', c.helper, 300000);
check('ask default 600s -> 660s', c.ask, 660000);

c = deadlines({ request_timeout_seconds: 777 }, 300000);
check('ask custom 777s -> 837s', c.ask, 837000);

c = deadlines({ request_timeout_seconds: 10 }, 300000);
check('small ask timeout keeps 300s floor', c.ask, 300000);
check('helper unaffected by small ask timeout', c.helper, 300000);

c = deadlines({}, 300000);
check('missing field -> 300s', c.ask, 300000);

c = deadlines({ request_timeout_seconds: 'x' }, 300000);
check('junk field -> 300s', c.ask, 300000);

c = deadlines({ request_timeout_seconds: 0 }, 300000);
check('zero field -> 300s', c.ask, 300000);

// The watchdog must re-evaluate the deadline each tick rather than freezing it.
if (!/now - entry\.startedAt >= root\.procTimeoutMsFor\(entry\.proc\)/.test(src)) {
  failures++;
  console.log('FAIL watchdog re-evaluates deadline dynamically');
} else {
  console.log('ok   watchdog re-evaluates deadline dynamically');
}

if (failures) {
  console.error(failures + ' failure(s)');
  process.exit(1);
}
console.log('all watchdog timeout tests passed');
