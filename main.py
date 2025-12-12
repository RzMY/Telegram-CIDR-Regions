import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

import requests
from netaddr import IPNetwork, AddrFormatError, cidr_merge

REGION_ASNS: Dict[str, List[int]] = {
    "SG": [44907, 62014],
    "US": [59930],
    "EU": [62041, 211157],
}

author = "RzMY"
repo_name = "Telegram-CIDR-Regions"
base_url = "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{asn}"
max_retries = 3
request_timeout = 15


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def fetch_prefixes(asn: int) -> List[str]:
    url = base_url.format(asn=asn)
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, timeout=request_timeout)
            response.raise_for_status()
            data = response.json()
            prefixes = data.get("data", {}).get("prefixes", [])
            extracted = [p.get("prefix") for p in prefixes if p.get("prefix")]
            logging.info("ASN %s fetched %d prefixes", asn, len(extracted))
            return extracted
        except (requests.RequestException, ValueError) as exc:
            wait_seconds = 2 ** (attempt - 1)
            logging.warning(
                "Attempt %d for ASN %s failed: %s (retrying in %ds)",
                attempt,
                asn,
                exc,
                wait_seconds,
            )
            if attempt < max_retries:
                time.sleep(wait_seconds)
    logging.error("Failed to fetch prefixes for ASN %s after %d attempts", asn, max_retries)
    return []


def gather_prefixes() -> Dict[str, List[str]]:
    region_prefixes: Dict[str, List[str]] = {region: [] for region in REGION_ASNS}
    futures = {}
    with ThreadPoolExecutor(max_workers=len(sum(REGION_ASNS.values(), []))) as executor:
        for region, asns in REGION_ASNS.items():
            for asn in asns:
                future = executor.submit(fetch_prefixes, asn)
                futures[future] = region
        for future in as_completed(futures):
            region = futures[future]
            try:
                result = future.result()
                region_prefixes[region].extend(result)
            except Exception as exc:  # noqa: BLE001
                logging.error("Unhandled exception when fetching for region %s: %s", region, exc)
    return region_prefixes


def split_and_merge(prefixes: Iterable[str]) -> Tuple[List[IPNetwork], List[IPNetwork]]:
    v4_networks: List[IPNetwork] = []
    v6_networks: List[IPNetwork] = []
    for prefix in prefixes:
        try:
            network = IPNetwork(prefix)
        except (AddrFormatError, ValueError) as exc:
            logging.warning("Skip invalid prefix %s: %s", prefix, exc)
            continue
        if network.version == 4:
            v4_networks.append(network)
        elif network.version == 6:
            v6_networks.append(network)
    merged_v4 = cidr_merge(v4_networks)
    merged_v6 = cidr_merge(v6_networks)
    return merged_v4, merged_v6


def sort_networks(networks: Iterable[IPNetwork]) -> List[IPNetwork]:
    return sorted(networks, key=lambda n: (n.version, int(n.network)))


def build_header(region: str, v4_count: int, v6_count: int) -> List[str]:
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    total = v4_count + v6_count
    return [
        f"# NAME: Telegram{region}",
        f"# AUTHOR: {author}",
        f"# REPO: https://github.com/{author}/{repo_name}",
        f"# UPDATED: {updated_at}",
        f"# IP-CIDR: {v4_count}",
        f"# IP6-CIDR: {v6_count}",
        f"# TOTAL: {total}",
    ]


def write_region_file(region: str, v4_networks: List[IPNetwork], v6_networks: List[IPNetwork]) -> None:
    file_name = f"Telegram{region}.list"
    v4_sorted = sort_networks(v4_networks)
    v6_sorted = sort_networks(v6_networks)
    lines = build_header(region, len(v4_sorted), len(v6_sorted))
    for net in v4_sorted:
        lines.append(f"IP-CIDR,{net},Telegram{region}")
    for net in v6_sorted:
        lines.append(f"IP6-CIDR,{net},Telegram{region}")
    with open(file_name, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    logging.info(
        "Wrote %s with %d IPv4 and %d IPv6 prefixes after merge",
        file_name,
        len(v4_sorted),
        len(v6_sorted),
    )


def main() -> int:
    setup_logging()
    logging.info("Starting Telegram CIDR update")
    prefix_map = gather_prefixes()
    for region, prefixes in prefix_map.items():
        v4_networks, v6_networks = split_and_merge(prefixes)
        write_region_file(region, v4_networks, v6_networks)
    logging.info("All region files generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
