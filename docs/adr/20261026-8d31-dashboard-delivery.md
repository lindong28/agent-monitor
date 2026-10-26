# Reduce dashboard transfer and cache validation

Accepted 2026-10-26. Scope: the existing dashboard HTTP server and its browser consumers. Preserve account actions, API payloads, snapshot admission, provider routing and data freshness behavior.

Browser measurements showed overview responses spending substantially longer in transfer than application processing, while cached JS/CSS still required remote validation on each navigation. The measurements also varied materially between runs, so a timing change alone cannot establish a code-level speedup.

Use versioned static URLs and useful-sized gzip responses within the existing service. The version binds relative paths and file contents, including supported vendor symlinks; traversal must terminate on directory cycles. HTML remains `no-store`. Only a request matching the current signature receives private immutable caching; obsolete and unversioned URLs retain `no-cache`. The document version is available before scripts execute, propagates to dynamic modules and seeds the existing health watcher. This preserves live asset update detection without introducing a service worker, proxy or persistent client data cache. It does not promise atomic deployments for already-open pages.

Keep a bounded in-process byte cache with file identity, size and nanosecond timestamp invalidation. Bind each response's advertised version to the same captured bytes used to serve it. Negotiate gzip without changing decoded content, use it only when smaller, and preserve representation-specific ETags, HEAD framing and conditional requests.

Unversioned TTL caching was rejected because it could retain obsolete code. Keeping validation on every navigation retains the observed round trips. A separate proxy/CDN or service worker adds lifecycle and state beyond this repair's needs. Exact runtime gains, cold and warm browser behavior, updates and dynamic imports require implementation and consumer verification; the decision's independent review does not establish those outcomes. Existing immutable responses cannot be revoked by changing server headers: new HTML must select a new version URL.
