// Host-static sanitizer tests for Model.js sanitizeTextForMarkdown (F5).
// Run: node test_model_sanitize.js   (pure JS, no Qt needed)
const fs = require('fs');
const vm = require('vm');

let src = fs.readFileSync(__dirname + '/Model.js', 'utf8');
// Model.js is a plain JS library (no Qt imports); evaluate it and capture functions.
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const sanitize = sandbox.sanitizeTextForMarkdown;
if (typeof sanitize !== 'function') { console.error('sanitizeTextForMarkdown not found'); process.exit(1); }

let failures = 0;
function check(name, input, mustNotContain, mustContain) {
  const out = sanitize(input);
  const bad = (mustNotContain || []).filter(x => out.includes(x));
  const missing = (mustContain || []).filter(x => !out.includes(x));
  const ok = !bad.length && !missing.length;
  if (!ok) failures++;
  console.log((ok ? 'ok  ' : 'FAIL') + ' ' + name);
  if (!ok) {
    console.log('   input:    ' + JSON.stringify(input));
    console.log('   output:   ' + JSON.stringify(out));
    if (bad.length) console.log('   contains: ' + JSON.stringify(bad));
    if (missing.length) console.log('   missing:  ' + JSON.stringify(missing));
  }
}

// Raw HTML including <a>/<img> must be escaped to literal text (no tag pass-through).
// The escaped text may still CONTAIN strings like "javascript:" — that is the fix:
// they render as inert visible text, never as a parsed link/handler.
check('raw img tag escaped', 'hi <img src="https://evil/x.png"> there', ['<img'], ['&lt;img']);
check('raw anchor tag escaped', '<a href="javascript:alert(1)">click</a>', ['<a '], ['&lt;a']);
check('self-closing img escaped', '<img src="https://evil/x.png"/>', ['<img'], ['&lt;img']);
// Inline + reference-style markdown images stripped (remote-image fetch vector)
check('inline md image stripped', 'look ![puppy](https://evil/p.png) here', ['![puppy](', 'https://evil/p.png)'], ['image removed']);
check('ref md image stripped', '![puppy][pic]\n\n[pic]: https://evil/p.png', ['![puppy][pic]'], ['image removed']);
// Legitimate text markdown survives
check('text link preserved', 'see [docs](https://example.com)', [], ['[docs](https://example.com)']);
check('bold italic preserved', '**bold** and *italic*', [], ['**bold**', '*italic*']);
check('inline code preserved', 'run `echo hello` now', [], ['echo hello']);
// Angle-bracket comparisons render as literal text via entities (never a tag)
check('comparison stays text', 'check x < 3 and y > 2', ['<img', '<a '], ['&lt;', '&gt;']);

// Permission-marker hardening: fenced/quoted/loose markers must not mint cards.
const detect = sandbox.detectPermissionRequest;
const strip = sandbox.stripFencedCode;
function pcheck(name, input, expectPerm) {
  const got = detect(input).isPermission;
  const ok = got === expectPerm;
  if (!ok) failures++;
  console.log((ok ? 'ok  ' : 'FAIL') + ' ' + name);
}
function scheck(name, input, mustNotContain) {
  const out = strip(input);
  const bad = (mustNotContain || []).filter(x => out.includes(x));
  if (bad.length) failures++;
  console.log((!bad.length ? 'ok  ' : 'FAIL') + ' ' + name);
}
pcheck('plain marker mints card', '🔒 SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x', true);
pcheck('fenced marker no card', '```\n🔒 SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x\n```', false);
pcheck('unclosed fence no card', '```\n🔒 SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x', false);
pcheck('quoted marker no card', '> 🔒 SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x', false);
pcheck('comment marker no card', '<!-- 🔒 SANDBOX PERMISSION REQUIRED: x -->', false);
pcheck('emoji-less marker no card', 'SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x', false);
pcheck('lookalike no card', '🔓 SANDBOX PERMISSION REQUIRED: rm -rf /tmp/x', false);
scheck('strip closed fence', 'a ```\ncode\n``` b', ['code']);
scheck('strip unclosed fence', 'a ```\ncode tail', ['code tail']);
if (typeof strip !== 'function') { failures++; console.log('FAIL stripFencedCode missing'); }
process.exit(failures ? 1 : 0);
