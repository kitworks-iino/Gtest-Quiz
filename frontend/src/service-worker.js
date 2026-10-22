const CACHE_NAME = 'gtest-quiz-static-gemini35_v1-000556647d7c-9af66045f7ee';
const CACHE_PREFIX = 'gtest-quiz-static-';
const QUESTION_BANK_URL = './question-bank.json';
const REVIEW_HOLDS_URL = './question-review-holds.json';
const SYLLABUS_URL = './official-syllabus.json';
const SHELL = [
  './index.html',
  './offline-app.js',
  './styles.css',
  './learning-engine.js',
  './content-guard.js',
  './ui.js',
  './syllabus.js',
  './official-syllabus.json',
  './manifest.webmanifest',
  './pwa-icon.svg',
  './pwa-icon-180.png',
  './pwa-icon-192.png',
  './pwa-icon-512.png',
  './assets/InterVariable.woff2',
  './question-bank.json',
  './question-review-holds.json',
];

self.addEventListener('install', (event) => {
  // Installation is atomic: offline use is enabled only with a complete shell
  // and bank. A failed update leaves the previous worker available.
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys
        .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request, response.clone());
  return response;
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  let networkResponse;
  try {
    networkResponse = await fetch(request);
    if (networkResponse.ok) {
      await cache.put(request, networkResponse.clone());
      return networkResponse;
    }
  } catch (_error) {
    // A cached navigation remains useful during an outage, including 5xx.
  }
  return (await cache.match(request))
    || (await cache.match('./index.html'))
    || networkResponse
    || new Response('Gtest is not yet available offline. Please reconnect.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
}

async function questionBankNetworkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const freshRequest = new Request(request, { cache: 'no-store' });
    const response = await fetch(freshRequest);
    if (response.ok) {
      // Never cache an HTML error page as the bank, even if a host sends 200.
      const payload = await response.clone().json();
      if (payload.schema_version === 'gtest_quiz_static_bank_v1' && Array.isArray(payload.questions)) {
        await cache.put(QUESTION_BANK_URL, response.clone());
        return response;
      }
    }
  } catch (_error) {
    // Network failures and invalid JSON use the last valid bank.
  }
  return (await cache.match(QUESTION_BANK_URL))
    || new Response(JSON.stringify({ error: 'question_bank_unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
}

async function reviewHoldsNetworkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(new Request(request, { cache: 'no-store' }));
    if (response.ok) {
      const payload = await response.clone().json();
      if (payload.schema_version === 'gtest_question_review_holds_v1'
        && typeof payload.source_bank_hash === 'string'
        && Array.isArray(payload.ids) && payload.ids.every((id) => typeof id === 'string')) {
        await cache.put(REVIEW_HOLDS_URL, response.clone());
        return response;
      }
    }
  } catch (_error) {
    // Keep the previously reviewed holds available offline.
  }
  return (await cache.match(REVIEW_HOLDS_URL))
    || new Response(JSON.stringify({ error: 'review_holds_unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
    });
}

async function syllabusNetworkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(new Request(request, { cache: 'no-store' }));
    if (response.ok) {
      const data = await response.clone().json();
      if (data.schema_version === 'gtest_official_syllabus_v1'
        && Number.isFinite(Date.parse(data.checked_at))
        && /^[a-f0-9]{64}$/.test(data.content_hash || '')
        && Array.isArray(data.sections) && data.sections.length > 0) {
        await cache.put(SYLLABUS_URL, response.clone());
        return response;
      }
    }
  } catch (_error) {
    // An old snapshot keeps its actual check date; never relabel it as current.
  }
  return (await cache.match(SYLLABUS_URL))
    || new Response(JSON.stringify({ error: 'syllabus_unavailable' }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const scopePath = new URL('./', self.location.href).pathname;
  if (!url.pathname.startsWith(scopePath)) return;
  const questionBankPath = new URL(QUESTION_BANK_URL, self.location.href).pathname;
  if (url.pathname === questionBankPath) {
    event.respondWith(questionBankNetworkFirst(request));
    return;
  }
  if (url.pathname === new URL(REVIEW_HOLDS_URL, self.location.href).pathname) {
    event.respondWith(reviewHoldsNetworkFirst(request));
    return;
  }
  if (url.pathname === new URL(SYLLABUS_URL, self.location.href).pathname) {
    event.respondWith(syllabusNetworkFirst(request));
    return;
  }
  if (request.mode === 'navigate' || url.pathname.endsWith('/index.html')) {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request));
});
