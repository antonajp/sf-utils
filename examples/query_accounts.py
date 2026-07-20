#!/usr/bin/env python3
"""Example: Query Salesforce accounts and display results.

Usage:
    python examples/query_accounts.py
    python examples/query_accounts.py --limit 5
"""

import argparse

from sf_utils import get_client, query_all


def main():
    parser = argparse.ArgumentParser(description="Query Salesforce accounts")
    parser.add_argument("--limit", type=int, default=10, help="Max records to fetch")
    args = parser.parse_args()

    # Auto-detects JWT vs password auth from environment
    client = get_client()
    print(f"Connected to: {client.sf_instance}")

    # Query accounts
    soql = f"SELECT Id, Name, Industry, CreatedDate FROM Account LIMIT {args.limit}"
    records = query_all(soql, client=client)

    print(f"\nFound {len(records)} accounts:\n")
    for record in records:
        print(f"  {record['Name']} ({record['Industry'] or 'No industry'})")


if __name__ == "__main__":
    main()
