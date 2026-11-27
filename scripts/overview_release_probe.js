// Installed before navigation in the verifier's private browser. Observe the
// payload consumed by this page without issuing a second statistics request.
function installReleaseOverview(expectedOrigin) {
  const reads = new Set(['/api/runtime-context', '/api/timezone', '/api/health',
    '/api/overview', '/api/sync-status', '/api/codex-accounts', '/api/quota-settings',
    '/api/quota-recovery']);
  const responses = new Map();
  let blocked = false, pageError = false;
  addEventListener('error', event => { if (event instanceof ErrorEvent) pageError = true; });
  addEventListener('unhandledrejection', () => { pageError = true; });
  const fetchPage = window.fetch.bind(window);
  window.fetch = async (input, options) => {
    const request = new Request(input, options);
    const url = new URL(request.url);
    if (location.origin !== expectedOrigin || location.pathname !== '/' || url.origin !== expectedOrigin || request.method !== 'GET'
        || (url.pathname.startsWith('/api/') && !reads.has(url.pathname))
        || url.searchParams.has('force')) {
      blocked = true;
      throw new Error('Release verification permits only dashboard reads');
    }
    const response = await fetchPage(request, {redirect: 'error'});
    if (url.pathname === '/api/overview') {
      const range = url.searchParams.get('range');
      const record = {status: response.status};
      responses.set(range, record);
      if (response.ok) {
        // Keep the account data inside the page; retain only the trend being verified.
        response.clone().json().then(data => {
          record.start = data.rollup_coverage?.range_start;
          record.end = data.attribution_windows?.week?.end;
          record.referenceDates = data.daily_cost_30d?.map(row => row.date);
          record.trend = data.tokens_over_time;
        }).catch(() => { record.invalid = true; });
      }
    }
    return response;
  };

  window.releaseOverview = {
    reset() { responses.clear(); },
    result(range, requireVisible = false) {
      const failure = reason => ({failure: reason});
      if (location.origin !== expectedOrigin || location.pathname !== '/') return failure('unexpected dashboard location');
      const record = responses.get(range);
      if (blocked) return failure('page attempted an action outside the dashboard reads');
      if (pageError) return failure('dashboard JavaScript error');
      if (document.querySelector('#overview-load-error')) return failure('dashboard reported a load error');
      if (record && record.status !== 200) return failure('overview HTTP ' + record.status);
      if (record?.invalid) return failure('overview returned invalid JSON');
      if (!record?.trend) return null;
      const table = document.querySelector('#daily-cost-data table');
      const days = Number.parseInt(range, 10);
      if (document.querySelector('#range')?.value !== range ||
          new URLSearchParams(location.search).get('range') !== range ||
          document.querySelector('#range-label')?.textContent !== '近 ' + days + ' 天成本' ||
          !table?.tBodies.length) return null;
      if (requireVisible && (!document.querySelector('#daily-cost-data').open
          || table.tBodies[0].getBoundingClientRect().height <= 0)) return null;
      const {rows, columns} = record.trend;
      if (!Array.isArray(rows) || !rows.length || !Array.isArray(columns) || !columns.length)
        return failure('no recorded token trend to verify');
      const visible = [...table.tBodies[0].rows].map(row => [...row.cells].map(cell => cell.textContent));
      const expected = rows.map(row => [row.x, ...columns.map(column => {
        const amount = row.values[column];
        return Number.isFinite(amount) ? amount.toLocaleString() + ' tokens' : '未知';
      })]);
      if (JSON.stringify(expected) !== JSON.stringify(visible)) return null;
      const dates = rows.map(row => row.x);
      const reference = record.referenceDates?.filter(day => day >= record.start && day <= record.end);
      if (JSON.stringify(dates) !== JSON.stringify(reference)) return failure('trend omits or adds recorded dates');
      return {dates, start: record.start, end: record.end};
    },
  };
}
