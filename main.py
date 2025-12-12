import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Tuple

import requests
from netaddr import IPNetwork, AddrFormatError, cidr_merge, cidr_exclude

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
            logging.info("ASN %s fetched %d prefixes: %s", asn, len(extracted), extracted)
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
    asn_futures = {}
    with ThreadPoolExecutor(max_workers=len(sum(REGION_ASNS.values(), []))) as executor:
        for region, asns in REGION_ASNS.items():
            for asn in asns:
                future = executor.submit(fetch_prefixes, asn)
                asn_futures[future] = (region, asn)
        
        # 收集所有前缀及其对应的 region 和 ASN
        all_networks: List[Tuple[IPNetwork, str, int]] = []  # (network, region, asn)
        
        for future in as_completed(asn_futures):
            region, asn = asn_futures[future]
            try:
                result = future.result()
                for prefix in result:
                    try:
                        network = IPNetwork(prefix)
                        all_networks.append((network, region, asn))
                    except (AddrFormatError, ValueError) as exc:
                        logging.warning("Invalid prefix %s from ASN %s: %s", prefix, asn, exc)
            except Exception as exc:  # noqa: BLE001
                logging.error("Unhandled exception when fetching for region %s: %s", region, exc)
        
        # 按 prefix length 降序排序（更具体的优先）
        all_networks.sort(key=lambda x: (x[0].version, -x[0].prefixlen, int(x[0].network)))
        
        # 去除重叠：保留更具体的网段，拆分重叠的更大网段
        final_networks: List[Tuple[IPNetwork, str, int]] = []
        for network, region, asn in all_networks:
            # 检查与已添加的更具体网段的重叠情况
            overlapping_nets = []
            for existing_net, existing_region, existing_asn in final_networks:
                if network.version == existing_net.version:
                    # 检查 existing_net 是否被 network 包含（network 更大，existing 更具体）
                    if existing_net in network and existing_region != region:
                        overlapping_nets.append((existing_net, existing_region, existing_asn))
            
            if overlapping_nets:
                # 从当前网段中排除所有已添加的更具体网段
                remaining_nets = [network]
                for overlap_net, overlap_region, overlap_asn in overlapping_nets:
                    new_remaining = []
                    for remain_net in remaining_nets:
                        if overlap_net in remain_net:
                            # 排除重叠部分
                            excluded = cidr_exclude(remain_net, overlap_net)
                            new_remaining.extend(excluded)
                            logging.info(
                                "Split %s/%d from %s (ASN %s): exclude %s/%d from %s (ASN %s), remaining: %s",
                                remain_net.network, remain_net.prefixlen, region, asn,
                                overlap_net.network, overlap_net.prefixlen, overlap_region, overlap_asn,
                                [str(n) for n in excluded]
                            )
                        else:
                            new_remaining.append(remain_net)
                    remaining_nets = new_remaining
                
                # 将拆分后的剩余网段添加到结果中
                for remain_net in remaining_nets:
                    final_networks.append((remain_net, region, asn))
            else:
                # 没有重叠，直接添加
                final_networks.append((network, region, asn))
        
        # 分配到各 region
        for network, region, _ in final_networks:
            region_prefixes[region].append(str(network))
    
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
        logging.info(
            "Processing region %s with %d prefixes (merging within region only)",
            region,
            len(prefixes),
        )
        v4_networks, v6_networks = split_and_merge(prefixes)
        write_region_file(region, v4_networks, v6_networks)
    logging.info("All region files generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
