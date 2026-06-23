#!/usr/bin/env python3
"""
Parse AUTOSAR ARXML and export searchable JSON/TXT/MD files.

Usage:
  python parse_arxml_export.py -i "xxx.arxml"
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


def basename_from_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def get_ns(root: ET.Element) -> tuple[str, dict[str, str]]:
    if root.tag.startswith("{") and "}" in root.tag:
        uri = root.tag.split("}", 1)[0][1:]
        return uri, {"ar": uri}
    return "", {"ar": ""}


def q(tag: str, ns_uri: str) -> str:
    if ns_uri:
        return f"{{{ns_uri}}}{tag}"
    return tag


def child_text(elem: ET.Element, tag: str, ns_uri: str) -> str | None:
    node = elem.find(q(tag, ns_uri))
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value if value else None


def deep_text(elem: ET.Element, path: list[str], ns_uri: str) -> str | None:
    cur = elem
    for item in path:
        nxt = cur.find(q(item, ns_uri))
        if nxt is None:
            return None
        cur = nxt
    if cur.text is None:
        return None
    value = cur.text.strip()
    return value if value else None


def direction_from_port_ref(ref: str | None) -> set[str]:
    directions: set[str] = set()
    if not ref:
        return directions
    low = ref.lower()
    if "_in" in low:
        directions.add("RX")
    if "_out" in low:
        directions.add("TX")
    return directions


def sort_numeric_text(value: str) -> tuple[int, int | str]:
    try:
        return (0, int(value, 0))
    except (TypeError, ValueError):
        return (1, value)


def normalize_communication_direction(value: str | None) -> str | None:
    if value == "IN":
        return "RX"
    if value == "OUT":
        return "TX"
    return None


def parse_port_directions(root: ET.Element, ns_uri: str, tag_name: str) -> dict[str, set[str]]:
    directions_by_port: dict[str, set[str]] = defaultdict(set)
    for connector in root.findall(f".//{q('CAN-COMMUNICATION-CONNECTOR', ns_uri)}"):
        connector_name = child_text(connector, "SHORT-NAME", ns_uri)
        for port in connector.findall(
            f"./{q('ECU-COMM-PORT-INSTANCES', ns_uri)}/{q(tag_name, ns_uri)}"
        ):
            port_name = child_text(port, "SHORT-NAME", ns_uri)
            direction = normalize_communication_direction(child_text(port, "COMMUNICATION-DIRECTION", ns_uri))
            if not port_name or not direction:
                continue
            directions_by_port[port_name].add(direction)
            if connector_name:
                directions_by_port[f"{connector_name}/{port_name}"].add(direction)
    return directions_by_port


def ensure_channel_meta(
    meta_by_name: dict[str, dict[str, dict[str, set[str]]]], name: str, channel: str
) -> dict[str, set[str]]:
    by_channel = meta_by_name.setdefault(name, {})
    if channel not in by_channel:
        by_channel[channel] = {"can_ids": set(), "directions": set()}
    return by_channel[channel]


def finalize_channel_meta(meta: dict[str, dict[str, set[str]]] | None) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for channel in sorted((meta or {}).keys()):
        info = meta[channel]
        out[channel] = {
            "can_ids": sorted(info.get("can_ids", set()), key=sort_numeric_text),
            "directions": sorted(info.get("directions", set())),
        }
    return out


def merge_channel_meta(
    target: dict[str, dict[str, set[str]]], source: dict[str, dict[str, list[str]]] | None
) -> dict[str, dict[str, set[str]]]:
    for channel, info in (source or {}).items():
        if channel not in target:
            target[channel] = {"can_ids": set(), "directions": set()}
        target[channel]["can_ids"].update(info.get("can_ids", []))
        target[channel]["directions"].update(info.get("directions", []))
    return target


def parse_pdu_triggering_map(root: ET.Element, ns_uri: str) -> dict[str, str]:
    trig_to_ipdu: dict[str, str] = {}
    for triggering in root.findall(f".//{q('PDU-TRIGGERING', ns_uri)}"):
        trig_name = child_text(triggering, "SHORT-NAME", ns_uri)
        ipdu_name = basename_from_ref(child_text(triggering, "I-PDU-REF", ns_uri))
        if trig_name and ipdu_name:
            trig_to_ipdu[trig_name] = ipdu_name
    return trig_to_ipdu


def parse_container_members(root: ET.Element, ns_uri: str, trig_to_ipdu: dict[str, str]) -> dict[str, list[str]]:
    container_members: dict[str, list[str]] = {}
    for container in root.findall(f".//{q('CONTAINER-I-PDU', ns_uri)}"):
        container_name = child_text(container, "SHORT-NAME", ns_uri)
        if not container_name:
            continue
        members: set[str] = set()
        for ref in container.findall(
            f"./{q('CONTAINED-PDU-TRIGGERING-REFS', ns_uri)}/{q('CONTAINED-PDU-TRIGGERING-REF', ns_uri)}"
        ):
            trig_name = basename_from_ref((ref.text or "").strip())
            if not trig_name:
                continue
            member_pdu = trig_to_ipdu.get(trig_name, trig_name)
            if member_pdu:
                members.add(member_pdu)
        container_members[container_name] = sorted(members)
    return container_members


def parse_can_frames(
    root: ET.Element, ns_uri: str, container_members: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    frames: list[dict[str, Any]] = []
    frames_by_pdu: dict[str, list[str]] = defaultdict(list)

    for frame in root.findall(f".//{q('CAN-FRAME', ns_uri)}"):
        frame_name = child_text(frame, "SHORT-NAME", ns_uri)
        frame_length = child_text(frame, "FRAME-LENGTH", ns_uri)
        mappings: list[dict[str, Any]] = []

        for mp in frame.findall(f".//{q('PDU-TO-FRAME-MAPPING', ns_uri)}"):
            pdu_ref = child_text(mp, "PDU-REF", ns_uri)
            pdu_name = basename_from_ref(pdu_ref)
            pdu_ref_node = mp.find(q("PDU-REF", ns_uri))
            pdu_dest = pdu_ref_node.get("DEST") if pdu_ref_node is not None else None
            contained_pdu_names = container_members.get(pdu_name, []) if pdu_dest == "CONTAINER-I-PDU" and pdu_name else []
            row = {
                "mapping_name": child_text(mp, "SHORT-NAME", ns_uri),
                "pdu_ref": pdu_ref,
                "pdu_name": pdu_name,
                "pdu_dest": pdu_dest,
                "contained_pdu_names": contained_pdu_names,
                "start_position": child_text(mp, "START-POSITION", ns_uri),
                "packing_byte_order": child_text(mp, "PACKING-BYTE-ORDER", ns_uri),
            }
            mappings.append(row)
            if pdu_name and frame_name:
                frames_by_pdu[pdu_name].append(frame_name)
            if frame_name:
                for member_pdu in contained_pdu_names:
                    frames_by_pdu[member_pdu].append(frame_name)

        frames.append(
            {
                "name": frame_name,
                "frame_length": frame_length,
                "pdu_mappings": mappings,
            }
        )

    for pdu_name, frame_names in frames_by_pdu.items():
        frames_by_pdu[pdu_name] = sorted(set(frame_names))

    frames.sort(key=lambda x: (x["name"] or ""))
    return frames, frames_by_pdu


def parse_can_channels(
    root: ET.Element, ns_uri: str
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], dict[str, dict[str, dict[str, set[str]]]]]:
    channels_by_frame: dict[str, set[str]] = defaultdict(set)
    ids_by_frame: dict[str, set[str]] = defaultdict(set)
    directions_by_frame: dict[str, set[str]] = defaultdict(set)
    channel_meta_by_frame: dict[str, dict[str, dict[str, set[str]]]] = {}
    frame_port_directions = parse_port_directions(root, ns_uri, "FRAME-PORT")

    for channel in root.findall(f".//{q('CAN-PHYSICAL-CHANNEL', ns_uri)}"):
        channel_name = child_text(channel, "SHORT-NAME", ns_uri)
        if not channel_name:
            continue
        for triggering in channel.findall(f".//{q('CAN-FRAME-TRIGGERING', ns_uri)}"):
            frame_ref = child_text(triggering, "FRAME-REF", ns_uri)
            frame_name = basename_from_ref(frame_ref) or child_text(triggering, "SHORT-NAME", ns_uri)
            if not frame_name:
                continue
            channels_by_frame[frame_name].add(channel_name)
            channel_meta = ensure_channel_meta(channel_meta_by_frame, frame_name, channel_name)

            can_id = child_text(triggering, "IDENTIFIER", ns_uri)
            if can_id:
                ids_by_frame[frame_name].add(can_id)
                channel_meta["can_ids"].add(can_id)

            for port_ref in triggering.findall(f"./{q('FRAME-PORT-REFS', ns_uri)}/{q('FRAME-PORT-REF', ns_uri)}"):
                port_ref_text = (port_ref.text or "").strip()
                directions = get_port_directions(frame_port_directions, port_ref_text)
                directions.update(direction_from_port_ref(port_ref_text))
                directions_by_frame[frame_name].update(directions)
                channel_meta["directions"].update(directions)

    return channels_by_frame, ids_by_frame, directions_by_frame, channel_meta_by_frame


def parse_pdus(root: ET.Element, ns_uri: str) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, set[str]]]:
    pdus: list[dict[str, Any]] = []
    signal_to_pdus: dict[str, set[str]] = defaultdict(set)
    signal_group_to_pdus: dict[str, set[str]] = defaultdict(set)

    for pdu in root.findall(f".//{q('I-SIGNAL-I-PDU', ns_uri)}"):
        pdu_name = child_text(pdu, "SHORT-NAME", ns_uri)
        pdu_length = child_text(pdu, "LENGTH", ns_uri)
        mappings: list[dict[str, Any]] = []

        for mp in pdu.findall(f"./{q('I-SIGNAL-TO-PDU-MAPPINGS', ns_uri)}/{q('I-SIGNAL-TO-I-PDU-MAPPING', ns_uri)}"):
            signal_ref = child_text(mp, "I-SIGNAL-REF", ns_uri)
            signal_name = basename_from_ref(signal_ref)
            signal_group_ref = child_text(mp, "I-SIGNAL-GROUP-REF", ns_uri)
            signal_group_name = basename_from_ref(signal_group_ref)
            row = {
                "mapping_name": child_text(mp, "SHORT-NAME", ns_uri),
                "signal_ref": signal_ref,
                "signal_name": signal_name,
                "signal_group_ref": signal_group_ref,
                "signal_group_name": signal_group_name,
                "start_position": child_text(mp, "START-POSITION", ns_uri),
                "update_indication_bit_position": child_text(mp, "UPDATE-INDICATION-BIT-POSITION", ns_uri),
                "byte_order": child_text(mp, "PACKING-BYTE-ORDER", ns_uri),
                "transfer_property": child_text(mp, "TRANSFER-PROPERTY", ns_uri),
            }
            mappings.append(row)

            if pdu_name and signal_name:
                signal_to_pdus[signal_name].add(pdu_name)
            if pdu_name and signal_group_name:
                signal_group_to_pdus[signal_group_name].add(pdu_name)

        pdus.append(
            {
                "name": pdu_name,
                "length": pdu_length,
                "signal_mappings": mappings,
            }
        )

    pdus.sort(key=lambda x: (x["name"] or ""))
    return pdus, signal_to_pdus, signal_group_to_pdus


def parse_signal_directions(root: ET.Element, ns_uri: str) -> dict[str, set[str]]:
    directions_by_signal: dict[str, set[str]] = defaultdict(set)
    signal_port_directions = parse_port_directions(root, ns_uri, "I-SIGNAL-PORT")
    for triggering in root.findall(f".//{q('I-SIGNAL-TRIGGERING', ns_uri)}"):
        signal_name = basename_from_ref(child_text(triggering, "I-SIGNAL-REF", ns_uri))
        if not signal_name:
            continue
        for port_ref in triggering.findall(f"./{q('I-SIGNAL-PORT-REFS', ns_uri)}/{q('I-SIGNAL-PORT-REF', ns_uri)}"):
            port_ref_text = (port_ref.text or "").strip()
            directions_by_signal[signal_name].update(get_port_directions(signal_port_directions, port_ref_text))
            directions_by_signal[signal_name].update(direction_from_port_ref(port_ref_text))
    return directions_by_signal


def parse_pdu_directions_from_ports(root: ET.Element, ns_uri: str) -> dict[str, set[str]]:
    directions_by_pdu: dict[str, set[str]] = defaultdict(set)
    pdu_port_directions = parse_port_directions(root, ns_uri, "I-PDU-PORT")
    for triggering in root.findall(f".//{q('PDU-TRIGGERING', ns_uri)}"):
        pdu_name = basename_from_ref(child_text(triggering, "I-PDU-REF", ns_uri)) or child_text(triggering, "SHORT-NAME", ns_uri)
        if not pdu_name:
            continue
        for port_ref in triggering.findall(f"./{q('I-PDU-PORT-REFS', ns_uri)}/{q('I-PDU-PORT-REF', ns_uri)}"):
            port_ref_text = (port_ref.text or "").strip()
            directions_by_pdu[pdu_name].update(get_port_directions(pdu_port_directions, port_ref_text))
            directions_by_pdu[pdu_name].update(direction_from_port_ref(port_ref_text))
    return directions_by_pdu


def get_port_directions(direction_map: dict[str, set[str]], ref_text: str) -> set[str]:
    keys = get_port_lookup_keys(ref_text)
    for key in keys:
        match = direction_map.get(key)
        if match:
            return set(match)
    return set()


def get_port_lookup_keys(ref_text: str) -> list[str]:
    parts = [p for p in ref_text.split("/") if p]
    keys = []
    if len(parts) >= 2:
        keys.append(f"{parts[-2]}/{parts[-1]}")
    if parts:
        keys.append(parts[-1])
    return keys


def parse_pdu_directions_from_groups(root: ET.Element, ns_uri: str) -> dict[str, set[str]]:
    pdu_directions: dict[str, set[str]] = defaultdict(set)
    for group in root.findall(f".//{q('I-SIGNAL-I-PDU-GROUP', ns_uri)}"):
        direction = normalize_communication_direction(child_text(group, "COMMUNICATION-DIRECTION", ns_uri))
        if not direction:
            continue
        for ref in group.findall(
            f"./{q('I-SIGNAL-I-PDUS', ns_uri)}/{q('I-SIGNAL-I-PDU-REF-CONDITIONAL', ns_uri)}/{q('I-SIGNAL-I-PDU-REF', ns_uri)}"
        ):
            pdu_name = basename_from_ref((ref.text or "").strip())
            if pdu_name:
                pdu_directions[pdu_name].add(direction)
    return pdu_directions


def parse_signals(root: ET.Element, ns_uri: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals: list[dict[str, Any]] = []
    signal_groups: list[dict[str, Any]] = []

    for sig in root.findall(f".//{q('I-SIGNAL', ns_uri)}"):
        name = child_text(sig, "SHORT-NAME", ns_uri)
        desc_node = sig.find(q("DESC", ns_uri))
        desc_text: str | None = None
        if desc_node is not None:
            texts = [t.strip() for t in desc_node.itertext() if t and t.strip()]
            if texts:
                desc_text = "\n".join(texts)

        value = deep_text(sig, ["INIT-VALUE", "NUMERICAL-VALUE-SPECIFICATION", "VALUE"], ns_uri)
        base_type_ref = deep_text(
            sig,
            [
                "NETWORK-REPRESENTATION-PROPS",
                "SW-DATA-DEF-PROPS-VARIANTS",
                "SW-DATA-DEF-PROPS-CONDITIONAL",
                "BASE-TYPE-REF",
            ],
            ns_uri,
        )
        compu_method_ref = deep_text(
            sig,
            [
                "NETWORK-REPRESENTATION-PROPS",
                "SW-DATA-DEF-PROPS-VARIANTS",
                "SW-DATA-DEF-PROPS-CONDITIONAL",
                "COMPU-METHOD-REF",
            ],
            ns_uri,
        )

        signals.append(
            {
                "name": name,
                "length": child_text(sig, "LENGTH", ns_uri),
                "system_signal_ref": child_text(sig, "SYSTEM-SIGNAL-REF", ns_uri),
                "data_type_policy": child_text(sig, "DATA-TYPE-POLICY", ns_uri),
                "init_value": value,
                "base_type_ref": base_type_ref,
                "compu_method_ref": compu_method_ref,
                "description": desc_text,
            }
        )

    for grp in root.findall(f".//{q('I-SIGNAL-GROUP', ns_uri)}"):
        group_name = child_text(grp, "SHORT-NAME", ns_uri)
        refs = grp.findall(f"./{q('I-SIGNAL-REFS', ns_uri)}/{q('I-SIGNAL-REF', ns_uri)}")
        signal_refs = [node.text.strip() for node in refs if node.text and node.text.strip()]
        signal_names = [basename_from_ref(x) for x in signal_refs if basename_from_ref(x)]
        signal_groups.append(
            {
                "name": group_name,
                "system_signal_group_ref": child_text(grp, "SYSTEM-SIGNAL-GROUP-REF", ns_uri),
                "signal_refs": signal_refs,
                "signal_names": signal_names,
            }
        )

    signals.sort(key=lambda x: (x["name"] or ""))
    signal_groups.sort(key=lambda x: (x["name"] or ""))
    return signals, signal_groups


def build_report(arxml_path: Path) -> dict[str, Any]:
    tree = ET.parse(arxml_path)
    root = tree.getroot()
    ns_uri, _ = get_ns(root)

    pdu_triggering_map = parse_pdu_triggering_map(root, ns_uri)
    container_members = parse_container_members(root, ns_uri, pdu_triggering_map)
    frames, frames_by_pdu = parse_can_frames(root, ns_uri, container_members)
    channels_by_frame, ids_by_frame, directions_by_frame, channel_meta_by_frame = parse_can_channels(root, ns_uri)
    pdus, signal_to_pdus, signal_group_to_pdus = parse_pdus(root, ns_uri)
    signal_directions = parse_signal_directions(root, ns_uri)
    pdu_directions_by_port = parse_pdu_directions_from_ports(root, ns_uri)
    pdu_directions_by_group = parse_pdu_directions_from_groups(root, ns_uri)
    signals, signal_groups = parse_signals(root, ns_uri)

    for frame in frames:
        frame_name = frame["name"] or ""
        frame["channel_names"] = sorted(channels_by_frame.get(frame_name, set()))
        frame["can_ids"] = sorted(ids_by_frame.get(frame_name, set()), key=sort_numeric_text)
        frame["directions"] = sorted(directions_by_frame.get(frame_name, set()))
        frame["channel_meta"] = finalize_channel_meta(channel_meta_by_frame.get(frame_name))

    pdu_map = {p["name"]: p for p in pdus if p.get("name")}
    for pdu_name, frame_names in frames_by_pdu.items():
        if pdu_name in pdu_map:
            pdu_map[pdu_name]["frame_names"] = frame_names
            channel_names: set[str] = set()
            can_ids: set[str] = set()
            directions: set[str] = set()
            channel_meta: dict[str, dict[str, set[str]]] = {}
            for frame_name in frame_names:
                channel_names.update(channels_by_frame.get(frame_name, set()))
                can_ids.update(ids_by_frame.get(frame_name, set()))
                directions.update(directions_by_frame.get(frame_name, set()))
                merge_channel_meta(channel_meta, next((f.get("channel_meta") for f in frames if f.get("name") == frame_name), None))
            pdu_map[pdu_name]["channel_names"] = sorted(channel_names)
            pdu_map[pdu_name]["can_ids"] = sorted(can_ids, key=sort_numeric_text)
            pdu_map[pdu_name]["directions"] = sorted(directions)
            pdu_map[pdu_name]["channel_meta"] = finalize_channel_meta(channel_meta)

    signal_map = {s["name"]: s for s in signals if s.get("name")}
    for signal_name, signal in signal_map.items():
        signal["directions"] = sorted(signal_directions.get(signal_name, set()))
    for signal_name, pdu_set in signal_to_pdus.items():
        row = signal_map.get(signal_name)
        if row is not None:
            row["pdu_names"] = sorted(pdu_set)
            frame_names: set[str] = set()
            for pdu_name in pdu_set:
                frame_names.update(frames_by_pdu.get(pdu_name, []))
            row["frame_names"] = sorted(frame_names)
            channel_names: set[str] = set()
            can_ids: set[str] = set()
            directions = set(row.get("directions", []))
            channel_meta: dict[str, dict[str, set[str]]] = {}
            for frame_name in frame_names:
                channel_names.update(channels_by_frame.get(frame_name, set()))
                can_ids.update(ids_by_frame.get(frame_name, set()))
                directions.update(directions_by_frame.get(frame_name, set()))
                merge_channel_meta(channel_meta, next((f.get("channel_meta") for f in frames if f.get("name") == frame_name), None))
            row["channel_names"] = sorted(channel_names)
            row["can_ids"] = sorted(can_ids, key=sort_numeric_text)
            row["directions"] = sorted(directions)
            row["channel_meta"] = finalize_channel_meta(channel_meta)

    for pdu in pdus:
        directions = set(pdu.get("directions", []))
        directions.update(pdu_directions_by_port.get(pdu["name"] or "", set()))
        by_group = pdu_directions_by_group.get(pdu["name"] or "", set())
        if by_group:
            directions.update(by_group)
        else:
            for mapping in pdu.get("signal_mappings", []):
                directions.update(signal_directions.get(mapping.get("signal_name") or "", set()))
        pdu["directions"] = sorted(directions)

    for frame in frames:
        directions = set(frame.get("directions", []))
        for mapping in frame.get("pdu_mappings", []):
            pdu_name = mapping.get("pdu_name")
            if pdu_name in pdu_map:
                directions.update(pdu_map[pdu_name].get("directions", []))
            for member_pdu in mapping.get("contained_pdu_names", []):
                if member_pdu in pdu_map:
                    directions.update(pdu_map[member_pdu].get("directions", []))
        frame["directions"] = sorted(directions)

    group_map = {g["name"]: g for g in signal_groups if g.get("name")}
    for group_name, pdu_set in signal_group_to_pdus.items():
        row = group_map.get(group_name)
        if row is not None:
            row["pdu_names"] = sorted(pdu_set)
            frame_names: set[str] = set()
            for pdu_name in pdu_set:
                frame_names.update(frames_by_pdu.get(pdu_name, []))
            row["frame_names"] = sorted(frame_names)
            channel_names: set[str] = set()
            can_ids: set[str] = set()
            directions: set[str] = set()
            channel_meta: dict[str, dict[str, set[str]]] = {}
            for frame_name in frame_names:
                channel_names.update(channels_by_frame.get(frame_name, set()))
                can_ids.update(ids_by_frame.get(frame_name, set()))
                directions.update(directions_by_frame.get(frame_name, set()))
                merge_channel_meta(channel_meta, next((f.get("channel_meta") for f in frames if f.get("name") == frame_name), None))
            for pdu_name in pdu_set:
                directions.update(pdu_map.get(pdu_name, {}).get("directions", []))
            row["channel_names"] = sorted(channel_names)
            row["can_ids"] = sorted(can_ids, key=sort_numeric_text)
            row["directions"] = sorted(directions)
            row["channel_meta"] = finalize_channel_meta(channel_meta)

    orphan_pdus = sorted([p["name"] for p in pdus if p.get("name") and not p.get("frame_names")])
    summary = {
        # Keep exported reports portable and avoid leaking local absolute paths.
        "source_file": arxml_path.name,
        "frame_count": len(frames),
        "pdu_count": len(pdus),
        "signal_count": len(signals),
        "signal_group_count": len(signal_groups),
        "pdus_without_frame": len(orphan_pdus),
        "frames_without_direction": len([f for f in frames if not f.get("directions")]),
        "pdus_without_direction": len([p for p in pdus if not p.get("directions")]),
        "signals_without_direction": len([s for s in signals if not s.get("directions")]),
    }

    return {
        "summary": summary,
        "frames": frames,
        "pdus": pdus,
        "signals": signals,
        "signal_groups": signal_groups,
        "pdus_without_frame": orphan_pdus,
    }


def build_search_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Search Index")
    lines.append("# Prefixes: FRAME | PDU | SIGNAL | SIGNAL_GROUP")
    lines.append("")

    for frame in report["frames"]:
        frame_name = frame.get("name") or ""
        pdu_names = sorted(
            {
                name
                for mapping in frame.get("pdu_mappings", [])
                for name in ([mapping.get("pdu_name")] + list(mapping.get("contained_pdu_names", [])))
                if name
            }
        )
        lines.append(
            f"FRAME|{frame_name}|length={frame.get('frame_length') or ''}|direction={','.join(frame.get('directions', []))}|pdus={','.join(pdu_names)}"
        )

    lines.append("")
    for pdu in report["pdus"]:
        pdu_name = pdu.get("name") or ""
        frame_names = ",".join(pdu.get("frame_names", []))
        signal_names = sorted(
            {
                m.get("signal_name")
                for m in pdu.get("signal_mappings", [])
                if m.get("signal_name")
            }
        )
        lines.append(
            f"PDU|{pdu_name}|length={pdu.get('length') or ''}|direction={','.join(pdu.get('directions', []))}|frames={frame_names}|signals={','.join(signal_names)}"
        )

    lines.append("")
    for sig in report["signals"]:
        name = sig.get("name") or ""
        pdu_names = ",".join(sig.get("pdu_names", []))
        frame_names = ",".join(sig.get("frame_names", []))
        lines.append(
            f"SIGNAL|{name}|length={sig.get('length') or ''}|direction={','.join(sig.get('directions', []))}|pdus={pdu_names}|frames={frame_names}|system={sig.get('system_signal_ref') or ''}"
        )

    lines.append("")
    for grp in report["signal_groups"]:
        name = grp.get("name") or ""
        pdu_names = ",".join(grp.get("pdu_names", []))
        frame_names = ",".join(grp.get("frame_names", []))
        signal_names = ",".join(grp.get("signal_names", []))
        lines.append(
            f"SIGNAL_GROUP|{name}|direction={','.join(grp.get('directions', []))}|pdus={pdu_names}|frames={frame_names}|signals={signal_names}"
        )

    return "\n".join(lines) + "\n"


def build_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines: list[str] = []
    lines.append("# ARXML 解析报告")
    lines.append("")
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- 源文件: `{summary['source_file']}`")
    lines.append(f"- Frame 数量: `{summary['frame_count']}`")
    lines.append(f"- PDU 数量: `{summary['pdu_count']}`")
    lines.append(f"- Signal 数量: `{summary['signal_count']}`")
    lines.append(f"- Signal Group 数量: `{summary['signal_group_count']}`")
    lines.append(f"- 未映射到 Frame 的 PDU: `{summary['pdus_without_frame']}`")
    lines.append(f"- 方向未知的 Frame: `{summary['frames_without_direction']}`")
    lines.append(f"- 方向未知的 PDU: `{summary['pdus_without_direction']}`")
    lines.append(f"- 方向未知的 Signal: `{summary['signals_without_direction']}`")
    lines.append("")
    lines.append("## PDU 明细")
    lines.append("")
    lines.append("| PDU | Length | Direction | Frames | Signal 数 |")
    lines.append("|---|---:|---|---|---:|")
    for pdu in report["pdus"]:
        pdu_name = pdu.get("name") or ""
        pdu_len = pdu.get("length") or ""
        pdu_direction = "/".join(pdu.get("directions", [])) or "UNKNOWN"
        frames = ", ".join(pdu.get("frame_names", []))
        sig_count = len([m for m in pdu.get("signal_mappings", []) if m.get("signal_name")])
        lines.append(f"| {pdu_name} | {pdu_len} | {pdu_direction} | {frames} | {sig_count} |")

    lines.append("")
    lines.append("## Signal 明细")
    lines.append("")
    lines.append("| Signal | Length | Direction | PDU 数 | Frame 数 | System Signal |")
    lines.append("|---|---:|---|---:|---:|---|")
    for sig in report["signals"]:
        lines.append(
            f"| {sig.get('name') or ''} | {sig.get('length') or ''} | {'/'.join(sig.get('directions', [])) or 'UNKNOWN'} | {len(sig.get('pdu_names', []))} | {len(sig.get('frame_names', []))} | {sig.get('system_signal_ref') or ''} |"
        )

    return "\n".join(lines) + "\n"


def write_text(path: Path, content: str) -> None:
    # utf-8-sig improves compatibility with Windows editors and avoids garbled text.
    path.write_text(content, encoding="utf-8-sig", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse AUTOSAR ARXML and export JSON/TXT/MD.")
    parser.add_argument("-i", "--input", required=True, help="Input ARXML path")
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Output directory (default: current directory)",
    )
    args = parser.parse_args()

    arxml_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not arxml_path.exists():
        raise FileNotFoundError(f"Input file not found: {arxml_path}")

    report = build_report(arxml_path)
    stem = arxml_path.stem

    json_path = out_dir / f"{stem}.parsed.json"
    txt_path = out_dir / f"{stem}.search.txt"
    md_path = out_dir / f"{stem}.report.md"

    write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2))
    write_text(txt_path, build_search_text(report))
    write_text(md_path, build_markdown(report))

    print(f"解析完成: {arxml_path}")
    print(f"输出 JSON: {json_path}")
    print(f"输出 TXT : {txt_path}")
    print(f"输出 MD  : {md_path}")
    print(
        "统计: "
        f"Frame={report['summary']['frame_count']}, "
        f"PDU={report['summary']['pdu_count']}, "
        f"Signal={report['summary']['signal_count']}, "
        f"SignalGroup={report['summary']['signal_group_count']}"
    )


if __name__ == "__main__":
    main()
