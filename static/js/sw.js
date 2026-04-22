/**
 * PHASE 5 — Service Worker for STEP Platform
 *
 * Implements:
 * - Offline-first caching strategy
 * - Cache-first for static assets (CSS, JS, images)
 * - Network-first for HTML pages
 * - Web Push notification handling
 *
 * Cache versioning allows for easy updates.
 */

const CACHE_VERSION = 'step-v1.0.0';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const DYNAMIC_CACHE = `${CACHE_VERSION}-dynamic`;

// Assets to cache immediately on install
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/ui.js',
  '/static/js/palette.js',
  '/static/manifest.json',
  // Add more critical assets as needed
];

// ============================================================================
// INSTALL EVENT - Cache static assets
// ============================================================================
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker...');

  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      console.log('[SW] Caching static assets');
      return cache.addAll(STATIC_ASSETS);
    }).then(() => {
      // Skip waiting to activate immediately
      return self.skipWaiting();
    })
  );
});

// ============================================================================
// ACTIVATE EVENT - Clean up old caches
// ============================================================================
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');

  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((cacheName) => {
            // Delete caches that don't match current version
            return cacheName.startsWith('step-') && cacheName !== STATIC_CACHE && cacheName !== DYNAMIC_CACHE;
          })
          .map((cacheName) => {
            console.log('[SW] Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          })
      );
    }).then(() => {
      // Take control of all pages immediately
      return self.clients.claim();
    })
  );
});

// ============================================================================
// FETCH EVENT - Serve from cache or network
// ============================================================================
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip chrome-extension and other non-http(s) requests
  if (!url.protocol.startsWith('http')) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cachedResponse) => {
      // STRATEGY: Cache-first for static assets
      if (isStaticAsset(url.pathname)) {
        if (cachedResponse) {
          return cachedResponse;
        }

        return fetch(request).then((networkResponse) => {
          return caches.open(STATIC_CACHE).then((cache) => {
            cache.put(request, networkResponse.clone());
            return networkResponse;
          });
        });
      }

      // STRATEGY: Network-first for HTML/API
      return fetch(request).then((networkResponse) => {
        // Cache successful responses
        if (networkResponse.status === 200) {
          return caches.open(DYNAMIC_CACHE).then((cache) => {
            cache.put(request, networkResponse.clone());
            return networkResponse;
          });
        }
        return networkResponse;
      }).catch(() => {
        // Fallback to cache if network fails
        if (cachedResponse) {
          return cachedResponse;
        }

        // Return offline page if available
        return caches.match('/offline.html');
      });
    })
  );
});

// ============================================================================
// PUSH NOTIFICATION EVENT - Handle incoming push messages
// ============================================================================
self.addEventListener('push', (event) => {
  console.log('[SW] Push notification received');

  let notificationData = {
    title: 'STEP Platform',
    body: 'You have a new notification',
    icon: '/static/icons/icon-192x192.png',
    badge: '/static/icons/icon-72x72.png',
    tag: 'step-notification',
    requireInteraction: false,
  };

  // Parse push data if available
  if (event.data) {
    try {
      const data = event.data.json();
      notificationData = {
        title: data.title || notificationData.title,
        body: data.body || data.message || notificationData.body,
        icon: data.icon || notificationData.icon,
        badge: notificationData.badge,
        tag: data.tag || notificationData.tag,
        data: data.url ? { url: data.url } : {},
        requireInteraction: data.requireInteraction || false,
      };
    } catch (e) {
      console.error('[SW] Error parsing push data:', e);
      notificationData.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(notificationData.title, {
      body: notificationData.body,
      icon: notificationData.icon,
      badge: notificationData.badge,
      tag: notificationData.tag,
      data: notificationData.data,
      requireInteraction: notificationData.requireInteraction,
    })
  );
});

// ============================================================================
// NOTIFICATION CLICK EVENT - Handle notification clicks
// ============================================================================
self.addEventListener('notificationclick', (event) => {
  console.log('[SW] Notification clicked');

  event.notification.close();

  const urlToOpen = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // Check if a window is already open
      for (const client of clientList) {
        if (client.url === urlToOpen && 'focus' in client) {
          return client.focus();
        }
      }

      // Open new window if none found
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/**
 * Check if a URL path is a static asset
 * @param {string} pathname - URL pathname
 * @returns {boolean}
 */
function isStaticAsset(pathname) {
  const staticExtensions = ['.css', '.js', '.png', '.jpg', '.jpeg', '.svg', '.gif', '.woff', '.woff2', '.ttf', '.eot'];
  return staticExtensions.some((ext) => pathname.endsWith(ext)) || pathname.startsWith('/static/');
}

/**
 * Check if request is for an API endpoint
 * @param {string} pathname - URL pathname
 * @returns {boolean}
 */
function isApiRequest(pathname) {
  return pathname.startsWith('/api/');
}
