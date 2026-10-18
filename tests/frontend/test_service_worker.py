"""Exercise offline/update behaviour against the actual service worker in Node."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")


@pytest.mark.skipif(not NODE, reason="Node is required for service worker behaviour checks")
def test_service_worker_offline_failure_and_update_behaviour():
    worker = Path("frontend/src/service-worker.js").resolve()
    harness = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const worker = fs.readFileSync(process.argv[1], 'utf8');
const base = 'https://example.test/Gtest-Quiz/service-worker.js';
const normalize = (request) => new URL(typeof request === 'string' ? request : request.url, base).href;
const stores = new Map();
const listeners = {};
let network;
let claimed = false;
let skipped = false;
const caches = {
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const store = stores.get(name);
    return {
      async match(request) { return store.get(normalize(request))?.clone(); },
      async put(request, response) { store.set(normalize(request), response.clone()); },
      async addAll(requests) {
        const responses = await Promise.all(requests.map(async url => {
          const response = await network(new Request(normalize(url)));
          if (!response.ok) throw new Error('precache failure');
          return [url, response];
        }));
        for (const [url, response] of responses) store.set(normalize(url), response.clone());
      },
    };
  },
  async keys() { return [...stores.keys()]; },
  async delete(name) { return stores.delete(name); },
};
const context = {
  URL, Request, Response, JSON, Promise, caches,
  fetch: (request) => network(request),
  self: {
    location: new URL(base),
    addEventListener: (name, listener) => listeners[name] = listener,
    skipWaiting: async () => skipped = true,
    clients: { claim: async () => claimed = true },
  },
};
vm.createContext(context);
vm.runInContext(worker, context);
const cacheName = vm.runInContext('CACHE_NAME', context);
const bankUrl = new URL('./question-bank.json', base).href;
const holdsUrl = new URL('./question-review-holds.json', base).href;
const syllabusUrl = new URL('./official-syllabus.json', base).href;
const syllabus = {schema_version:'gtest_official_syllabus_v1', checked_at:'2026-10-18T07:00:00Z',
  content_hash:'a'.repeat(64), sections:[{title:'AI'}]};
const syllabusResponse = () => new Response(JSON.stringify(syllabus), {headers:{'Content-Type':'application/json'}});
const bank = {schema_version: 'gtest_quiz_static_bank_v1', meta: {}, questions: [{id:'offline'}]};
const bankResponse = () => new Response(JSON.stringify(bank), {headers:{'Content-Type':'application/json'}});
const holds = {schema_version:'gtest_question_review_holds_v1', source_bank_hash:'audited-bank', ids:['held']};
const holdsResponse = () => new Response(JSON.stringify(holds), {headers:{'Content-Type':'application/json'}});
async function fetchThroughWorker(url, method = 'GET') {
  let result;
  listeners.fetch({request:new Request(url, {method}), respondWith:promise => result = promise});
  return result === undefined ? undefined : await result;
}
(async () => {
  network = async request => request.url === bankUrl ? bankResponse()
    : request.url === holdsUrl ? holdsResponse()
    : request.url === syllabusUrl ? syllabusResponse() : new Response('shell');
  let installed;
  listeners.install({waitUntil: promise => installed = promise});
  await installed;
  assert.ok(skipped);
  const cache = await caches.open(cacheName);
  assert.equal((await (await cache.match('./question-bank.json')).json()).questions[0].id, 'offline');
  for (const resource of ['./index.html','./offline-app.js','./styles.css','./learning-engine.js','./content-guard.js','./assets/InterVariable.woff2','./question-review-holds.json']) {
    assert.ok(await cache.match(resource), `missing offline asset ${resource}`);
  }

  // Failures include HTTP errors, not just rejected fetch promises.
  for (const failure of [
    async () => { throw new Error('offline'); },
    async () => new Response('service unavailable', {status:503}),
    async () => new Response('<html>error</html>', {headers:{'Content-Type':'text/html'}}),
  ]) {
    network = failure;
    const response = await fetchThroughWorker(bankUrl);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).questions[0].id, 'offline');
    assert.deepEqual((await (await fetchThroughWorker(holdsUrl)).json()).ids, ['held']);
    assert.deepEqual(await (await fetchThroughWorker(syllabusUrl)).json(), syllabus);
  }
  // Cache-busting bank requests use the same known, valid offline bank.
  network = async () => { throw new Error('offline'); };
  assert.equal((await (await fetchThroughWorker(bankUrl + '?refresh=1')).json()).questions[0].id, 'offline');

  await cache.put('./index.html', new Response('<html>app</html>', {headers:{'Content-Type':'text/html'}}));
  network = async () => new Response('maintenance', {status:503});
  assert.equal(await (await fetchThroughWorker(new URL('./index.html', base))).text(), '<html>app</html>');

  // A bank miss is JSON/503 and must never return the cached index document.
  stores.get(cacheName).delete(bankUrl);
  const unavailable = await fetchThroughWorker(bankUrl);
  assert.equal(unavailable.status, 503);
  assert.match(unavailable.headers.get('Content-Type'), /application\/json/);
  assert.equal((await unavailable.json()).error, 'question_bank_unavailable');
  stores.get(cacheName).delete(holdsUrl);
  const missingHolds = await fetchThroughWorker(holdsUrl);
  assert.equal(missingHolds.status, 503);
  assert.equal((await missingHolds.json()).error, 'review_holds_unavailable');
  stores.get(cacheName).delete(syllabusUrl);
  assert.equal((await fetchThroughWorker(syllabusUrl)).status, 503);
  network = async request => {
    assert.equal(request.cache, 'no-store');
    return syllabusResponse();
  };
  assert.deepEqual(await (await fetchThroughWorker(syllabusUrl)).json(), syllabus);
  network = async () => new Response(JSON.stringify({schema_version:'wrong',sections:[]}));
  assert.deepEqual(await (await fetchThroughWorker(syllabusUrl)).json(), syllabus);

  network = async request => {
    assert.equal(request.cache, 'no-store');
    return bankResponse();
  };
  assert.equal((await (await fetchThroughWorker(bankUrl)).json()).questions[0].id, 'offline');
  network = async () => { throw new Error('offline again'); };
  assert.equal((await (await fetchThroughWorker(bankUrl)).json()).questions[0].id, 'offline');

  // Activation only removes previous versions of this application's cache.
  stores.set('gtest-quiz-static-old', new Map());
  stores.set('unrelated-app-cache', new Map());
  let activated;
  listeners.activate({waitUntil: promise => activated = promise});
  await activated;
  assert.ok(claimed);
  assert.ok(!stores.has('gtest-quiz-static-old'));
  assert.ok(stores.has('unrelated-app-cache'));
  assert.ok(stores.has(cacheName));

  assert.equal(await fetchThroughWorker('https://other.test/question-bank.json'), undefined);
  assert.equal(await fetchThroughWorker('https://example.test/another-app/question-bank.json'), undefined);
  assert.equal(await fetchThroughWorker(bankUrl, 'POST'), undefined);
  console.log(JSON.stringify({passed:true}));
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run([NODE, "-e", harness, str(worker)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"]
