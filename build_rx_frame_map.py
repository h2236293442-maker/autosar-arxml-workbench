#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET


def q(tag: str, ns: str) -> str:
    return f"{{{ns}}}{tag}" if ns else tag


def t(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    s = node.text.strip()
    return s or None


def base(ref: str | None) -> str | None:
    if not ref:
        return None
    return ref.rsplit("/", 1)[-1]


def find_ns(root: ET.Element) -> str:
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag.split("}", 1)[0][1:]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True, help="arxml path")
    ap.add_argument("-o", "--output", required=True, help="json output path")
    ap.add_argument("--channels", required=True, help="comma separated channels")
    args = ap.parse_args()

    xml_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()
    target_channels = [x.strip() for x in args.channels.split(",") if x.strip()]

    tree = ET.parse(xml_path)
    root = tree.getroot()
    ns = find_ns(root)

    # PDU-TRIGGERING short-name -> I-PDU short-name
    trig_to_ipdu: dict[str, str] = {}
    for tr in root.findall(f".//{q('PDU-TRIGGERING', ns)}"):
        tr_name = t(tr.find(q("SHORT-NAME", ns)))
        ipdu_ref = t(tr.find(q("I-PDU-REF", ns)))
        ipdu_name = base(ipdu_ref)
        if tr_name and ipdu_name:
            trig_to_ipdu[tr_name] = ipdu_name

    # PDU direction from I-SIGNAL-I-PDU-GROUP COMMUNICATION-DIRECTION (authoritative).
    # If absent, fallback will be inferred from signal port suffix later.
    pdu_dir: dict[str, set[str]] = defaultdict(set)
    for grp in root.findall(f".//{q('I-SIGNAL-I-PDU-GROUP', ns)}"):
        cdir = t(grp.find(q("COMMUNICATION-DIRECTION", ns)))
        if not cdir:
            continue
        direction = "RX" if cdir.upper() == "IN" else ("TX" if cdir.upper() == "OUT" else None)
        if not direction:
            continue
        for rc in grp.findall(
            f"./{q('I-SIGNAL-I-PDUS', ns)}/{q('I-SIGNAL-I-PDU-REF-CONDITIONAL', ns)}/{q('I-SIGNAL-I-PDU-REF', ns)}"
        ):
            pdu_name = base(t(rc))
            if pdu_name:
                pdu_dir[pdu_name].add(direction)

    # Fallback: infer PDU direction from I-SIGNAL-TRIGGERING port suffix if group info missing.
    sig_dir: dict[str, set[str]] = defaultdict(set)
    for st in root.findall(f".//{q('I-SIGNAL-TRIGGERING', ns)}"):
        sig_name = base(t(st.find(q("I-SIGNAL-REF", ns))))
        if not sig_name:
            continue
        for pr in st.findall(f"./{q('I-SIGNAL-PORT-REFS', ns)}/{q('I-SIGNAL-PORT-REF', ns)}"):
            ref = t(pr)
            if not ref:
                continue
            low = ref.lower()
            if "_in" in low:
                sig_dir[sig_name].add("RX")
            if "_out" in low:
                sig_dir[sig_name].add("TX")

    pdu_sigs: dict[str, set[str]] = defaultdict(set)
    for pdu in root.findall(f".//{q('I-SIGNAL-I-PDU', ns)}"):
        pdu_name = t(pdu.find(q("SHORT-NAME", ns)))
        if not pdu_name:
            continue
        for m in pdu.findall(f"./{q('I-SIGNAL-TO-PDU-MAPPINGS', ns)}/{q('I-SIGNAL-TO-I-PDU-MAPPING', ns)}"):
            sn = base(t(m.find(q("I-SIGNAL-REF", ns))))
            if sn:
                pdu_sigs[pdu_name].add(sn)
        if pdu_name not in pdu_dir:
            for sn in pdu_sigs[pdu_name]:
                pdu_dir[pdu_name].update(sig_dir.get(sn, set()))

    # CONTAINER-I-PDU -> member PDU names
    container_members: dict[str, list[str]] = defaultdict(list)
    for c in root.findall(f".//{q('CONTAINER-I-PDU', ns)}"):
        cname = t(c.find(q("SHORT-NAME", ns)))
        if not cname:
            continue
        members: list[str] = []
        for r in c.findall(
            f"./{q('CONTAINED-PDU-TRIGGERING-REFS', ns)}/{q('CONTAINED-PDU-TRIGGERING-REF', ns)}"
        ):
            trig_name = base(t(r))
            if not trig_name:
                continue
            members.append(trig_to_ipdu.get(trig_name, trig_name))
        container_members[cname] = sorted(set(members))

    # Channel -> frame -> pdu set via CAN-FRAME-TRIGGERING
    rows: list[dict] = []
    for ch in root.findall(f".//{q('CAN-PHYSICAL-CHANNEL', ns)}"):
        ch_name = t(ch.find(q("SHORT-NAME", ns)))
        if not ch_name:
            continue
        if ch_name not in target_channels:
            continue
        for ft in ch.findall(f".//{q('CAN-FRAME-TRIGGERING', ns)}"):
            frame_name = base(t(ft.find(q("FRAME-REF", ns)))) or t(ft.find(q("SHORT-NAME", ns)))
            if not frame_name:
                continue
            pdu_names: set[str] = set()
            for rr in ft.findall(
                f"./{q('PDU-TRIGGERINGS', ns)}/{q('PDU-TRIGGERING-REF-CONDITIONAL', ns)}/{q('PDU-TRIGGERING-REF', ns)}"
            ):
                trig_name = base(t(rr))
                if not trig_name:
                    continue
                ipdu = trig_to_ipdu.get(trig_name, trig_name)
                members = container_members.get(ipdu)
                if members:
                    pdu_names.update(members)
                else:
                    pdu_names.add(ipdu)

            # fallback from Communication/Frames
            if not pdu_names:
                for f in root.findall(f".//{q('CAN-FRAME', ns)}"):
                    if t(f.find(q("SHORT-NAME", ns))) != frame_name:
                        continue
                    for mp in f.findall(f".//{q('PDU-TO-FRAME-MAPPING', ns)}"):
                        ref = mp.find(q("PDU-REF", ns))
                        ipdu = base(t(ref))
                        if not ipdu:
                            continue
                        dest = ref.get("DEST") if ref is not None else None
                        if dest == "CONTAINER-I-PDU" and ipdu in container_members:
                            pdu_names.update(container_members[ipdu])
                        else:
                            pdu_names.add(ipdu)
                    break

            frame_dir = set()
            for pr in ft.findall(f"./{q('FRAME-PORT-REFS', ns)}/{q('FRAME-PORT-REF', ns)}"):
                r = t(pr)
                if not r:
                    continue
                low = r.lower()
                if "_in" in low:
                    frame_dir.add("RX")
                if "_out" in low:
                    frame_dir.add("TX")

            all_pdus = sorted(pdu_names)
            rx_pdus = sorted([p for p in all_pdus if "RX" in pdu_dir.get(p, set())])
            tx_pdus = sorted([p for p in all_pdus if "TX" in pdu_dir.get(p, set())])

            frame_is_rx = ("RX" in frame_dir) or bool(rx_pdus)
            if frame_is_rx:
                rows.append(
                    {
                        "channel": ch_name,
                        "frame": frame_name,
                        "all_pdus": all_pdus,
                        "frame_direction": sorted(frame_dir),
                        "rx_pdus": rx_pdus,
                        "tx_pdus": tx_pdus,
                    }
                )

    # Keep only unique frame per channel
    uniq = {}
    for r in rows:
        uniq[(r["channel"], r["frame"])] = r
    rows = [uniq[k] for k in sorted(uniq.keys())]

    # Optional channel alias map. Keep it empty in the public version to avoid
    # publishing project-specific bus names. Customize it locally if needed.
    channel_alias: dict[str, str] = {}
    alias_rows = []
    for r in rows:
        alias_rows.append(r)
    channels_found = sorted(set(r["channel"] for r in alias_rows))

    out = {
        # Keep generated JSON portable and avoid leaking local absolute paths.
        "source_arxml": xml_path.name,
        "target_channels": target_channels,
        "channel_alias": channel_alias,
        "channels_found": channels_found,
        "rows": alias_rows,
        "multi_rx_pdu_frames": [
            {"channel": r["channel"], "frame": r["frame"], "rx_pdu_count": len(r["rx_pdus"]), "rx_pdus": r["rx_pdus"]}
            for r in alias_rows
            if len(r["rx_pdus"]) > 1
        ],
    }

    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output: {out_path}")
    print(f"rows: {len(alias_rows)}")
    print(f"channels_found: {', '.join(channels_found)}")
    print(f"multi_rx_pdu_frames: {len(out['multi_rx_pdu_frames'])}")


if __name__ == "__main__":
    main()
