#!/usr/bin/env python3
"""Small HTTP/HTML SEO auditor. Standard library only; no website changes.

Author: Ishant Sharma, Hustle Marketers (https://hustlemarketers.com)
Source: https://github.com/hustlemarketers-art/seo-audit-toolkit (MIT License)
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

USER_AGENT = 'HustleMarketersAudit/1.0 (+https://github.com/hustlemarketers-art/seo-audit-toolkit)'
MAX_BYTES = 5 * 1024 * 1024


def normalize_url(url: str) -> str:
    return urldefrag(url.strip())[0]


def origin(url: str) -> str:
    p = urlsplit(url)
    return f'{p.scheme.lower()}://{p.netloc.lower()}'


def valid_url(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme in {'http', 'https'} and bool(p.netloc) and not p.username and not p.password


@dataclass
class Response:
    requested_url: str
    final_url: str
    status: int | None
    headers: dict
    body: bytes
    error: str = ''
    truncated: bool = False

    def text(self) -> str:
        match = re.search(r'charset\s*=\s*["\']?([\w.-]+)', self.headers.get('content-type', ''), re.I)
        try:
            return self.body.decode(match.group(1) if match else 'utf-8', errors='replace')
        except LookupError:
            return self.body.decode('utf-8', errors='replace')


def fetch(url: str, timeout: float = 15) -> Response:
    if not valid_url(url):
        return Response(url, url, None, {}, b'', 'Only HTTP(S) URLs without credentials are supported.')
    try:
        response = urlopen(Request(url, headers={'User-Agent': USER_AGENT}), timeout=timeout)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError, ValueError) as error:
        return Response(url, url, None, {}, b'', str(error))
    try:
        with response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            data = response.read(MAX_BYTES + 1)
            truncated = len(data) > MAX_BYTES
            data = data[:MAX_BYTES]
            if data[:2] == b'\x1f\x8b' and not truncated:
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                    data = compressed.read(MAX_BYTES + 1)
                truncated = len(data) > MAX_BYTES
                data = data[:MAX_BYTES]
            return Response(url, response.geturl(), response.code, headers, data, truncated=truncated)
    except (OSError, EOFError, ValueError) as error:
        return Response(url, url, None, {}, b'', str(error))


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles, self.h1s, self.descriptions, self.canonicals = [], [], [], []
        self.robots, self.links, self.jsonld = [], [], []
        self.base = ''
        self.images = 0
        self.images_missing_alt = 0
        self._title = self._h1 = None
        self._json = None
        self._hidden_depth = 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        tag = tag.lower()
        if tag == 'title': self._title = []
        if tag == 'h1': self._h1 = []
        if tag == 'base' and not self.base: self.base = a.get('href', '')
        if tag == 'meta':
            name = a.get('name', '').lower()
            if name == 'description': self.descriptions.append(a.get('content', ''))
            if name in {'robots', 'googlebot'}: self.robots.append(name + ': ' + a.get('content', ''))
        if tag == 'link' and 'canonical' in a.get('rel', '').lower().split():
            self.canonicals.append(a.get('href', ''))
        if tag == 'a' and a.get('href'): self.links.append(a['href'])
        if tag == 'img':
            self.images += 1
            if 'alt' not in a: self.images_missing_alt += 1
        if tag in {'script', 'style'}:
            self._hidden_depth += 1
        if tag == 'script' and a.get('type', '').split(';')[0].strip().lower() == 'application/ld+json':
            self._json = []

    def handle_endtag(self, tag):
        if tag == 'title' and self._title is not None:
            self.titles.append(' '.join(''.join(self._title).split()))
            self._title = None
        if tag == 'h1' and self._h1 is not None:
            self.h1s.append(' '.join(''.join(self._h1).split()))
            self._h1 = None
        if tag == 'script' and self._json is not None:
            self.jsonld.append(''.join(self._json))
            self._json = None
        if tag in {'script', 'style'}:
            self._hidden_depth = max(0, self._hidden_depth - 1)

    def handle_data(self, data):
        if self._title is not None: self._title.append(data)
        if self._h1 is not None: self._h1.append(data)
        if self._json is not None: self._json.append(data)
        if not self._hidden_depth: self._text.append(data)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def schema_details(blocks):
    types, errors, empty_graphs = set(), 0, 0

    def visit(node):
        nonlocal empty_graphs
        if isinstance(node, list):
            for value in node: visit(value)
        elif isinstance(node, dict):
            t = node.get('@type', [])
            if isinstance(t, str): types.add(t)
            elif isinstance(t, list): types.update(x for x in t if isinstance(x, str))
            if '@graph' in node and node['@graph'] == []: empty_graphs += 1
            for value in node.values():
                if isinstance(value, (dict, list)): visit(value)

    def invalid_constant(value):
        raise ValueError('Invalid JSON numeric constant: ' + value)

    for block in blocks:
        try: visit(json.loads(block, parse_constant=invalid_constant))
        except (ValueError, TypeError, RecursionError): errors += 1
    return sorted(types), errors, empty_graphs


def inspect_html(response):
    parser = PageParser()
    parser.feed(response.text())
    parser.close()
    base = urljoin(response.final_url, parser.base) if parser.base else response.final_url
    canonicals = [normalize_url(urljoin(base, value)) for value in parser.canonicals if value.strip()]
    links = sorted({normalize_url(urljoin(base, value)) for value in parser.links if valid_url(urljoin(base, value))})
    types, schema_errors, empty_graphs = schema_details(parser.jsonld)
    directive = ' | '.join(parser.robots)
    header_directive = response.headers.get('x-robots-tag', '')
    return {
        'title': parser.titles[0] if parser.titles else '', 'title_count': len(parser.titles),
        'title_characters': len(parser.titles[0]) if parser.titles else 0,
        'meta_description': parser.descriptions[0] if parser.descriptions else '',
        'meta_description_count': len(parser.descriptions),
        'h1_count': len(parser.h1s), 'h1': ' | '.join(parser.h1s),
        'canonical': canonicals[0] if canonicals else '', 'canonical_count': len(parser.canonicals),
        'robots_meta': directive, 'x_robots_tag': header_directive,
        'noindex_directive_present': bool(re.search(r'\bnoindex\b', directive + ' ' + header_directive, re.I)),
        'jsonld_blocks': len(parser.jsonld), 'jsonld_errors': schema_errors,
        'jsonld_empty_graphs': empty_graphs, 'schema_types': ' | '.join(types),
        'image_count': parser.images, 'images_missing_alt_attribute': parser.images_missing_alt,
        'approximate_html_word_count': len(re.findall(r'\b\w+\b', ' '.join(parser._text))),
        'links': links,
    }


class RobotsRules:
    def __init__(self, site, timeout, delay):
        self.url = site.rstrip('/') + '/robots.txt'
        time.sleep(delay)
        result = fetch(self.url, timeout)
        self.parser = RobotFileParser()
        self.problem = ''
        if result.status == 404:
            self.parser.parse([])
        elif result.status == 200 and not result.truncated:
            self.parser.parse(result.text().splitlines())
        else:
            self.problem = 'Robots rules unavailable: ' + str(result.status or result.error)

    def allows(self, url, agent=USER_AGENT):
        return not self.problem and self.parser.can_fetch(agent, url)


def sitemap_urls(sitemap, site, robots, timeout, delay, max_sitemaps=100):
    queue, visited, pages, problems = deque([sitemap]), set(), set(), []
    while queue and len(visited) < max_sitemaps:
        url = normalize_url(queue.popleft())
        if url in visited: continue
        visited.add(url)
        if origin(url) != site:
            problems.append({'url': url, 'detail': 'Cross-origin sitemap not followed.'})
            continue
        if not robots.allows(url):
            problems.append({'url': url, 'detail': robots.problem or 'Sitemap disallowed for audit crawler.'})
            continue
        time.sleep(delay)
        response = fetch(url, timeout)
        if response.status != 200 or response.error or response.truncated:
            problems.append({'url': url, 'detail': 'Sitemap fetch failed or exceeded size cap: ' + str(response.status or response.error)})
            continue
        try:
            root = ET.fromstring(response.body)
        except ET.ParseError as error:
            problems.append({'url': url, 'detail': 'Invalid XML: ' + str(error)})
            continue
        kind = root.tag.split('}')[-1]
        if kind not in {'urlset', 'sitemapindex'}:
            problems.append({'url': url, 'detail': 'Expected urlset or sitemapindex.'})
            continue
        for child in root:
            for value in child:
                if value.tag.split('}')[-1] != 'loc' or not value.text: continue
                target = normalize_url(urljoin(response.final_url, value.text.strip()))
                if not valid_url(target): continue
                if kind == 'sitemapindex': queue.append(target)
                else: pages.add(target)
    if queue: problems.append({'url': sitemap, 'detail': 'Sitemap cap reached; discovery incomplete.'})
    return sorted(pages), problems


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            safe = {key: ("'" + value if isinstance(value, str) and re.match(r'^\s*[=+@-]', value) else value) for key, value in row.items()}
            writer.writerow(safe)


def run_audit(seeds, site, robots, output, timeout=15, delay=0.3, max_pages=200, crawl_links=False):
    queue = deque(normalize_url(url) for url in seeds)
    seen, pages, issues, links = set(), [], [], []
    sitemap_set = set(queue)

    def issue(url, code, detail, severity='review'):
        issues.append({'url': url, 'code': code, 'severity': severity, 'detail': detail})

    while queue and len(pages) < max_pages:
        url = queue.popleft()
        if url in seen: continue
        seen.add(url)
        if not valid_url(url) or origin(url) != site:
            issue(url, 'OUT_OF_SCOPE_URL', 'Only the configured origin is crawled.', 'info')
            continue
        record = {'url': url, 'in_input_sitemap_or_list': url in sitemap_set}
        if not robots.allows(url):
            record.update({'crawl_result': 'SKIPPED_ROBOTS', 'http_status': ''})
            issue(url, 'AUDIT_CRAWL_SKIPPED', robots.problem or 'Disallowed for ' + USER_AGENT)
            pages.append(record)
            continue
        record['robots_allows_googlebot'] = robots.allows(url, 'Googlebot')
        time.sleep(delay)
        response = fetch(url, timeout)
        record.update({'final_url': response.final_url, 'http_status': response.status or '',
                       'crawl_result': 'FETCHED' if not response.error else 'FETCH_ERROR', 'fetch_error': response.error})
        pages.append(record)
        if response.error:
            issue(url, 'FETCH_ERROR', response.error)
            continue
        if response.status != 200:
            code = 'HTTP_ERROR' if response.status and response.status >= 400 else 'NON_200_HTTP'
            issue(url, code, 'Final response HTTP ' + str(response.status))
            continue
        if normalize_url(response.final_url) != url:
            issue(url, 'URL_REDIRECTS', 'Final URL: ' + response.final_url)
        if response.truncated:
            issue(url, 'BODY_SIZE_CAP', 'HTML was not parsed because the response exceeded the size cap.')
            continue
        if origin(response.final_url) != site:
            issue(url, 'REDIRECT_OUT_OF_SCOPE', 'Content on another origin was not parsed.')
            continue
        content_type = response.headers.get('content-type', '')
        if 'html' not in content_type.lower():
            record['crawl_result'] = 'NON_HTML'
            issue(url, 'NON_HTML_CONTENT', content_type or 'No content type supplied.', 'info')
            continue
        details = inspect_html(response)
        destinations = details.pop('links')
        record.update(details)
        if not details['title']: issue(url, 'MISSING_TITLE', 'No nonempty HTML title found.')
        if details['title_count'] > 1: issue(url, 'MULTIPLE_TITLES', 'Multiple title elements found.')
        if not details['meta_description']: issue(url, 'MISSING_META_DESCRIPTION', 'Review snippet text; a meta description is not an indexing requirement.')
        if details['meta_description_count'] > 1: issue(url, 'MULTIPLE_META_DESCRIPTIONS', 'Multiple description tags found.')
        if details['h1_count'] == 0: issue(url, 'NO_H1', 'Review the main heading and page structure.')
        if details['h1_count'] > 1: issue(url, 'MULTIPLE_H1_REVIEW', 'Multiple H1s are a structure review, not an automatic SEO error.', 'info')
        if not details['canonical']: issue(url, 'NO_HTML_CANONICAL', 'Review canonical signals. HTTP-header canonicals are not parsed.', 'info')
        elif details['canonical'] != normalize_url(response.final_url): issue(url, 'CANONICAL_TO_OTHER_URL', details['canonical'])
        if details['canonical_count'] > 1: issue(url, 'MULTIPLE_CANONICALS', 'More than one HTML canonical link found.')
        if details['noindex_directive_present']: issue(url, 'NOINDEX_DIRECTIVE_PRESENT', 'Review the exact robots meta and X-Robots-Tag fields, including any bot-specific scope.')
        if not record['robots_allows_googlebot']: issue(url, 'GOOGLEBOT_ROBOTS_DISALLOW', 'robots.txt disallows this URL for Googlebot. This does not by itself prove deindexing.')
        if details['jsonld_errors']: issue(url, 'INVALID_JSONLD', str(details['jsonld_errors']) + ' JSON-LD blocks failed JSON parsing.')
        if details['jsonld_empty_graphs']: issue(url, 'EMPTY_JSONLD_GRAPH', str(details['jsonld_empty_graphs']) + ' empty @graph arrays found; review all schema blocks.')
        if not details['jsonld_blocks']: issue(url, 'NO_JSONLD_FOUND', 'No JSON-LD in fetched HTML. Other formats or JavaScript-injected markup may exist.', 'info')
        if details['images_missing_alt_attribute']: issue(url, 'IMAGES_WITHOUT_ALT_ATTRIBUTE', str(details['images_missing_alt_attribute']) + ' images have no alt attribute. Blank alt for decorative images is accepted.')
        for target in destinations:
            if origin(target) != site: continue
            links.append({'source_url': url, 'target_url': target})
            if crawl_links and target not in seen: queue.append(target)

    for field, code in [('title', 'DUPLICATE_TITLE'), ('meta_description', 'DUPLICATE_META_DESCRIPTION')]:
        groups = defaultdict(list)
        for page in pages:
            if page.get(field): groups[page[field]].append(page['url'])
        for group in groups.values():
            if len(group) > 1:
                for url in group: issue(url, code, 'Shared by ' + str(len(group)) + ' crawled URLs; review intent before changing pages.')
    by_url = {page['url']: page for page in pages}
    for link in links:
        target = by_url.get(link['target_url'])
        link['target_http_status'] = target.get('http_status', '') if target else ''
        link['check_result'] = target.get('crawl_result', 'NOT_CHECKED') if target else 'NOT_CHECKED'
        if target and target.get('http_status') in {404, 410}:
            issue(link['source_url'], 'INTERNAL_LINK_TO_404_OR_410', link['target_url'])
    output.mkdir(parents=True, exist_ok=True)
    page_fields = ['url','final_url','http_status','crawl_result','fetch_error','in_input_sitemap_or_list','robots_allows_googlebot','title','title_count','title_characters','meta_description','meta_description_count','h1_count','h1','canonical','canonical_count','robots_meta','x_robots_tag','noindex_directive_present','jsonld_blocks','jsonld_errors','jsonld_empty_graphs','schema_types','image_count','images_missing_alt_attribute','approximate_html_word_count']
    write_csv(output/'pages.csv', pages, page_fields)
    write_csv(output/'issues.csv', issues, ['url','code','severity','detail'])
    write_csv(output/'internal-links.csv', links, ['source_url','target_url','target_http_status','check_result'])
    summary = {
        'generated_utc': datetime.now(timezone.utc).isoformat(), 'site': site,
        'pages_recorded': len(pages), 'issues': len(issues),
        'issue_counts': dict(Counter(row['code'] for row in issues)),
        'remaining_queued_unique_urls': len(set(queue) - seen),
        'max_pages': max_pages, 'crawl_limit_reached': bool(set(queue) - seen),
        'method': 'HTTP response and fetched HTML only; no JavaScript rendering or Search Console indexing verification.',
        'unchecked_internal_links': sum(row['check_result'] == 'NOT_CHECKED' for row in links),
        'robots_problem': robots.problem,
    }
    (output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sitemap', help='Exact sitemap or sitemap index URL (plain or gzipped XML).')
    parser.add_argument('--urls-file', type=Path, help='UTF-8 file with one URL per line.')
    parser.add_argument('--site', help='Optional origin; inferred from the sitemap or first URL.')
    parser.add_argument('--output', type=Path, default=Path('seo-audit-output'))
    parser.add_argument('--max-pages', type=int, default=200)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--delay', type=float, default=0.3)
    parser.add_argument('--crawl-links', action='store_true', help='Also crawl same-origin internal links up to max-pages.')
    args = parser.parse_args()
    if not args.sitemap and not args.urls_file: parser.error('Provide --sitemap or --urls-file.')
    if args.max_pages < 1 or args.timeout <= 0 or args.delay < 0: parser.error('Limits and timeout must be positive; delay must be zero or more.')
    seeds = []
    if args.urls_file:
        seeds = [normalize_url(line) for line in args.urls_file.read_text(encoding='utf-8-sig').splitlines() if line.strip() and not line.lstrip().startswith('#')]
    first = args.site or args.sitemap or (seeds[0] if seeds else '')
    if not valid_url(first): parser.error('No valid HTTP(S) site URL supplied.')
    site = origin(first)
    robots = RobotsRules(site, args.timeout, args.delay)
    delay = max(args.delay, float(robots.parser.crawl_delay(USER_AGENT) or 0))
    discovery = []
    if args.sitemap:
        discovered, discovery = sitemap_urls(args.sitemap, site, robots, args.timeout, delay)
        seeds.extend(discovered)
    summary = run_audit(seeds, site, robots, args.output, args.timeout, delay, args.max_pages, args.crawl_links)
    write_csv(args.output/'sitemap-discovery-issues.csv', discovery, ['url','detail'])
    summary['sitemap_discovery_issues'] = len(discovery)
    summary['input_urls_discovered'] = len(set(seeds))
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    if not seeds or summary['pages_recorded'] == 0 or robots.problem:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
