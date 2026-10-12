# SEO Audit Toolkit: Technical SEO Crawler and Keyword Cannibalization Finder

Two free Python scripts for technical SEO work:

1. **`seo_audit.py`** crawls an XML sitemap (or a URL list) and checks status codes, redirects, titles, meta descriptions, H1s, canonicals, robots/noindex directives, JSON-LD structured data, image alt text and internal links.
2. **`gsc_query_overlap.py`** reads a Google Search Console query + page export and finds queries where several of your pages compete, the usual starting point for a keyword cannibalization review.

Python 3.10+, standard library only: no `pip install`, login or API key.

Built by [Ishant Sharma](https://ishantsharmamarketer.com/), SEO and Google Ads specialist and founder of [Hustle Marketers](https://hustlemarketers.com/).

## 1. Sitemap and page audit

```sh
python3 seo_audit.py --sitemap "https://www.example.com/sitemap.xml" --max-pages 500 --crawl-links --output reports
```

It follows sitemap indexes and reads gzip-compressed sitemaps. Use the sitemap your site actually publishes (often listed in `robots.txt`).

Or audit a list of URLs, one per line:

```sh
python3 seo_audit.py --urls-file examples/urls.txt --output reports
```

### What it checks

- Final HTTP status and whether an input URL redirects
- Title and meta description presence, plus exact duplicates across pages
- H1 structure and HTML canonical links
- Robots meta tags, `X-Robots-Tag` headers and `robots.txt` rules
- JSON-LD syntax, declared `@type`s and empty `@graph` arrays
- Images missing an `alt` attribute (a blank decorative `alt=""` is accepted)
- Internal link destinations, with an explicit status for links that were not checked

### Output files

| File | Contents |
| --- | --- |
| `pages.csv` | One row per URL: status, title, description, canonical, directives, schema types and image counts. |
| `issues.csv` | Review items with URL, issue code, severity and detail. |
| `internal-links.csv` | Same-site links and destination status where checked. |
| `sitemap-discovery-issues.csv` | XML, fetch, scope or sitemap-cap problems. |
| `summary.json` | Scope, counts, crawl caps and method notes. |

### Polite crawling

The crawler obeys `robots.txt` (including a crawl delay), stays on the starting origin, waits 0.3 seconds between requests by default and stops at 200 pages unless you raise `--max-pages`. If `robots.txt` cannot be fetched safely, the crawl pauses and records the problem; a 404 `robots.txt` allows the audit.

### Limits

It inspects the HTML the server returns, not a JavaScript-rendered page. That is also roughly what most AI crawlers see, so content that only appears after JavaScript runs is worth checking separately. It cannot confirm Google-selected canonicals, index status, Core Web Vitals or rich-result eligibility. Missing canonicals, multiple H1s and missing descriptions are review items, not automatic indexing failures.

## 2. Search Console query overlap (keyword cannibalization check)

`gsc_query_overlap.py` lists queries with meaningful impressions on two or more pages. It does not label them as cannibalization automatically: check search intent, page roles and performance before merging or redirecting content.

The input needs **Query, Page, Clicks and Impressions in the same table**, for example a Search Analytics API export with the `query` and `page` dimensions together. The standard Search Console export puts queries and pages on separate tabs, which cannot show which query belongs to which page.

```sh
python3 gsc_query_overlap.py combined-query-page-export.csv --min-impressions 50 --output query-overlap.csv
```

Try it with the fictional example:

```sh
python3 gsc_query_overlap.py examples/synthetic-query-page-export.csv --min-impressions 10 --output sample-query-overlap.csv
```

Thresholds apply to each query/page pair after rows are aggregated. Impression share is calculated across the candidate pages only. Search Console privacy filtering and export limits can leave the input incomplete.

## Tests

```sh
python3 -m unittest -v test_seo.py
```

Eight tests run the crawler against a local fixture server with nested and gzipped sitemaps, a redirect, a 404, a robots-disallowed path, a noindex page and JSON-LD errors, and check the query overlap aggregation. No live website is touched.

## Official references

- [Google SEO guide for developers](https://developers.google.com/search/docs/fundamentals/get-started-developers)
- [Canonical URLs](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Robots meta tag and X-Robots-Tag](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag)
- [Build and submit a sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Search Analytics API query](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)

## Related

- [google-ads-scripts](https://github.com/hustlemarketers-art/google-ads-scripts): read-only Google Ads audit and reporting scripts.
- [Schema Checker](https://hustlemarketers.com/tools/schema-checker/) and [SEO ROI Calculator](https://hustlemarketers.com/tools/seo-roi-calculator/), free tools from Hustle Marketers.
- [SEO case studies](https://hustlemarketers.com/case-study/) with the methods behind them.

## Author

**Ishant Sharma** (Chandigarh, India) has worked in SEO and paid search since 2013 and leads [Hustle Marketers](https://hustlemarketers.com/), a digital marketing agency covering technical SEO, local SEO, ecommerce SEO and AI search visibility. [Get in touch](https://hustlemarketers.com/contact-us/).

## License

[MIT](LICENSE). All Search Console example data is fictional.
