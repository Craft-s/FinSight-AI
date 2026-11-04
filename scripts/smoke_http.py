"""Read-only smoke check for a running snapshot deployment; no credentials needed."""
import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        parser.error('--base-url must be a credential-free HTTP(S) origin')
    opener = build_opener(ProxyHandler({}))

    def get(path, expected=200, json_body=True):
        try:
            response = opener.open(Request(base + path), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == expected, f'Unexpected status for {path}: {response.status}'
            raw = response.read(2_000_001)
            assert len(raw) <= 2_000_000, f'Response too large for {path}'
            return json.loads(raw) if json_body else raw

    health = get('/health')
    assert health['status'] == 'ok' and health['database'] == health['redis'] == 'ok'
    assert health['source_kind'] == 'sec_snapshot'
    results = get('/api/v1/companies/search?' + urlencode({'q': 'AAPL', 'market': 'US'}))
    assert any(item['cik'] == '0000320193' for item in results['items'])
    company = get('/api/v1/companies/0000320193')
    assert company['name'] == 'Apple Inc.' and company['source_kind'] == 'sec_snapshot'
    metrics = get('/api/v1/companies/0000320193/metrics?frequency=annual&years=1')
    assert metrics['items'] and any(item['metric_key'] == 'revenue' and isinstance(item['value'], str)
                                    for item in metrics['items'])
    filings = get('/api/v1/companies/0000320193/filings?type=10-K&page_size=50')['items']
    filing = next(item for item in filings if item['accession_number'] == '0000320193-24-000123')
    documents = get(f"/api/v1/filings/{filing['id']}/documents")['items']
    assert documents and documents[0]['source_kind'] == 'sec_snapshot'
    evidence = get('/api/v1/companies/0000320193/evidence?' + urlencode({'q': '风险', 'filing_id': filing['id']}))
    assert evidence['items'] and evidence['retrieval_mode'] == 'postgres_fts'
    assert all(item['document_sha256'] == documents[0]['sha256'] and item['excerpt']
               for item in evidence['items'])
    assert get('/api/v1/research/history', expected=401)['error']['code'] == 'AUTH_REQUIRED'
    assert b'id="app"' in get('/company/0000320193', json_body=False)
    print(json.dumps({'status': 'passed', 'source_kind': 'sec_snapshot', 'company': company['ticker'],
                      'metric_count': len(metrics['items']), 'evidence_count': len(evidence['items']),
                      'private_history_requires_authorization': True}))


if __name__ == '__main__':
    main()
