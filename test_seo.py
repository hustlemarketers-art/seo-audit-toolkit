import csv
import gzip
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from seo_audit import Response, RobotsRules, inspect_html, schema_details, sitemap_urls, run_audit
from gsc_query_overlap import find_overlaps


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self):
        base = 'http://127.0.0.1:' + str(self.server.server_port)
        body, status, mime = b'', 200, 'text/html; charset=utf-8'
        if self.path == '/robots.txt':
            body, mime = b'User-agent: *\nDisallow: /blocked\n', 'text/plain'
        elif self.path == '/index.xml':
            body = ('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>' + base + '/pages.xml.gz</loc></sitemap></sitemapindex>').encode()
            mime = 'application/xml'
        elif self.path == '/pages.xml.gz':
            paths = ['/a','/b','/redirect','/broken','/blocked','/empty-schema','/bad-schema']
            body = gzip.compress(('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join('<url><loc>'+base+path+'</loc></url>' for path in paths) + '</urlset>').encode())
            mime = 'application/gzip'
        elif self.path == '/redirect':
            self.send_response(301); self.send_header('Location','/a'); self.end_headers(); return
        elif self.path == '/broken':
            status, body = 404, b'Not found'
        elif self.path == '/a':
            body = ('<title>Shared title</title><meta name="description" content="Description A"><link rel="canonical" href="'+base+'/a"><h1>Page A</h1><a href="/broken">Broken</a><a href="https://example.org/outside">Outside</a><img src="x"><img src="y" alt=""><script type="application/ld+json">{"@type":"WebPage"}</script>').encode()
        elif self.path == '/b':
            body = b'<title>Shared title</title><meta name="description" content="Description B"><meta name="robots" content="noindex, follow"><link rel="canonical" href="/a"><h1>Page B</h1>'
        elif self.path == '/empty-schema':
            body = b'<title>Empty graph</title><h1>Empty graph</h1><script type="application/ld+json">{"@context":"https://schema.org","@graph":[]}</script>'
        elif self.path == '/bad-schema':
            body = b'<title>Bad JSON</title><h1>Bad JSON</h1><script type="application/ld+json">{broken}</script>'
        else: body = b'<title>Other</title><h1>Other</h1>'
        self.send_response(status); self.send_header('Content-Type',mime); self.end_headers(); self.wfile.write(body)


class SEOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_html_parser_handles_relative_canonical_and_decorative_alt(self):
        body = b'<title>A &amp; B</title><link rel="canonical" href="/target"><h1>Main <span>heading</span></h1><img src="a" alt=""><img src="b"><a href="/test#part">A</a>'
        report = inspect_html(Response(self.base+'/a',self.base+'/a',200,{},body))
        self.assertEqual(report['title'],'A & B')
        self.assertEqual(report['h1'],'Main heading')
        self.assertEqual(report['canonical'],self.base+'/target')
        self.assertEqual(report['images_missing_alt_attribute'],1)
        self.assertEqual(report['links'],[self.base+'/test'])

    def test_jsonld_graphs_and_strict_invalid_json(self):
        types, errors, empty = schema_details(['{"@graph":[]}','{"@type":["Person","Organization"]}','{broken}','{"number":NaN}'])
        self.assertEqual(types,['Organization','Person'])
        self.assertEqual(errors,2)
        self.assertEqual(empty,1)

    def test_nested_and_gzipped_sitemap(self):
        robots = RobotsRules(self.base,5,0)
        pages, issues = sitemap_urls(self.base+'/index.xml',self.base,robots,5,0)
        self.assertEqual(len(pages),7)
        self.assertEqual(issues,[])
        self.assertFalse(robots.allows(self.base+'/blocked'))

    def test_full_audit_reports_redirect_schema_noindex_and_broken_links(self):
        robots = RobotsRules(self.base,5,0)
        pages, _ = sitemap_urls(self.base+'/index.xml',self.base,robots,5,0)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            summary = run_audit(pages,self.base,robots,output,5,0,100,False)
            codes = summary['issue_counts']
            for expected in ['URL_REDIRECTS','NOINDEX_DIRECTIVE_PRESENT','EMPTY_JSONLD_GRAPH','INVALID_JSONLD','AUDIT_CRAWL_SKIPPED','INTERNAL_LINK_TO_404_OR_410','DUPLICATE_TITLE']:
                self.assertIn(expected,codes)
            with (output/'pages.csv').open(encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            blocked = next(row for row in rows if row['url'].endswith('/blocked'))
            self.assertEqual(blocked['crawl_result'],'SKIPPED_ROBOTS')
            self.assertTrue((output/'internal-links.csv').exists())

    def test_page_limit_is_explicit(self):
        robots = RobotsRules(self.base,5,0)
        with tempfile.TemporaryDirectory() as temporary:
            summary = run_audit([self.base+'/a',self.base+'/b'],self.base,robots,Path(temporary),5,0,1,False)
            self.assertTrue(summary['crawl_limit_reached'])
            self.assertEqual(summary['pages_recorded'],1)

    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable,str(Path(__file__).with_name('seo_audit.py')),'--sitemap',self.base+'/index.xml','--delay','0','--output',temporary],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['input_urls_discovered'],7)
            self.assertTrue((Path(temporary)/'sitemap-discovery-issues.csv').exists())

    def test_query_overlap_aggregates_rows_and_respects_impression_threshold(self):
        rows = [
            {'Query':'Google Ads specialist','Page':'https://example.com/a','Clicks':'5','Impressions':'100'},
            {'Query':'google ads specialist','Page':'https://example.com/a','Clicks':'2','Impressions':'30'},
            {'Query':'google ads specialist','Page':'https://example.com/b','Clicks':'1','Impressions':'20'},
            {'Query':'google ads specialist','Page':'https://example.com/c','Clicks':'0','Impressions':'2'},
            {'Query':'other query','Page':'https://example.com/a','Clicks':'0','Impressions':'40'},
        ]
        result = find_overlaps(rows,10)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['impressions'],130)
        self.assertEqual(result[0]['clicks'],7)
        self.assertEqual(result[0]['pages_sharing_query'],2)
        self.assertIn('not proof of cannibalization',result[0]['review'])

    def test_query_overlap_rejects_bad_values(self):
        for bad in ['-1','NaN','Infinity','one']:
            with self.assertRaises(ValueError):
                find_overlaps([{'Query':'a','Page':'b','Clicks':bad,'Impressions':'10'}])


if __name__ == '__main__': unittest.main()
