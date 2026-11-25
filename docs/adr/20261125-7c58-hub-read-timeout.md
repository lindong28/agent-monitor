# Recover a statistics read after a transient Hub connection timeout

A client overview intermittently returned HTTP 502 while its local page still loaded. The product bridge recorded `proxy_connect_timeout` before forwarding the read. The same Hub and query later supplied a valid overview without a service restart. The underlying network interruption remains unexplained.

Retry the allowlisted statistics GET once through the existing adapter when the bridge reports this specific connection failure. The bridge removes its reserved response headers from upstream replies, so an ordinary Hub 502 retains its original behavior. Normal reads still make one request. Two failed attempts return a connection timeout to the reader. A visible overview can still recover on its existing refresh interval.

Waiting for the next page refresh leaves the first view empty during recoverable failures. Persisting a previous overview would introduce freshness and account-data handling requirements and would not help a first visit. Account actions keep their existing replay policy.
