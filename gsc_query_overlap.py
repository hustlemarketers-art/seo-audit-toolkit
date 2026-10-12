#!/usr/bin/env python3
"""Find queries shared by several pages in a combined Search Console export.

Author: Ishant Sharma, Hustle Marketers (https://hustlemarketers.com)
Source: https://github.com/hustlemarketers-art/seo-audit-toolkit (MIT License)
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


def find_overlaps(rows, minimum_impressions=10):
    if not math.isfinite(minimum_impressions) or minimum_impressions < 0:
        raise ValueError('The minimum impressions threshold must be finite and nonnegative.')
    groups = defaultdict(lambda: defaultdict(lambda: {'clicks': 0.0, 'impressions': 0.0}))
    for row in rows:
        lowered = {key.strip().lower(): value for key, value in row.items() if key is not None}
        query, page = lowered.get('query', '').strip(), lowered.get('page', '').strip()
        if not query or not page: raise ValueError('Every row must contain Query and Page values.')
        try:
            clicks = float(lowered.get('clicks', 0) or 0)
            impressions = float(lowered.get('impressions', 0) or 0)
        except ValueError as error:
            raise ValueError('Clicks and Impressions must be numbers without thousands separators.') from error
        if not math.isfinite(clicks) or not math.isfinite(impressions) or clicks < 0 or impressions < 0:
            raise ValueError('Clicks and Impressions must be finite and nonnegative.')
        item = groups[query.casefold()][page]
        item['clicks'] += clicks
        item['impressions'] += impressions
    result = []
    for query, pages in groups.items():
        eligible = [(page, stats) for page, stats in pages.items() if stats['impressions'] >= minimum_impressions]
        if len(eligible) < 2: continue
        total = sum(stats['impressions'] for _, stats in eligible)
        for page, stats in sorted(eligible, key=lambda item: item[1]['impressions'], reverse=True):
            result.append({'query': query, 'page': page, 'clicks': stats['clicks'],
                           'impressions': stats['impressions'], 'pages_sharing_query': len(eligible),
                           'impression_share_among_eligible_pages': stats['impressions'] / total if total else None,
                           'review': 'SHARED_QUERY_REVIEW_INTENT; not proof of cannibalization'})
    return sorted(result, key=lambda row: (-row['pages_sharing_query'], row['query'], -row['impressions']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_csv', type=Path)
    parser.add_argument('--min-impressions', type=float, default=10)
    parser.add_argument('--output', type=Path, default=Path('query-overlap.csv'))
    args = parser.parse_args()
    if not math.isfinite(args.min_impressions) or args.min_impressions < 0: parser.error('min-impressions must be finite and zero or more.')
    with args.input_csv.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        fields = {field.strip().lower() for field in reader.fieldnames or []}
        if not {'query','page','clicks','impressions'}.issubset(fields):
            parser.error('Input must have Query, Page, Clicks and Impressions in the SAME table.')
        result = find_overlaps(reader, args.min_impressions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ['query','page','clicks','impressions','pages_sharing_query','impression_share_among_eligible_pages','review']
    with args.output.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in result:
            safe = {key: ("'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')) else value) for key,value in row.items()}
            writer.writerow(safe)
    print(json.dumps({'output': str(args.output), 'review_rows': len(result)}))


if __name__ == '__main__':
    main()
