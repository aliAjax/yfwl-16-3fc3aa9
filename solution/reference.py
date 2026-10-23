#!/usr/bin/env python3
"""Recover an unlabeled queue network and calibrate deterministic throughput."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path


TOP_KEYS = {"schema_version", "case_id", "candidates", "experiments", "edges", "probes"}
CANDIDATE_KEYS = {"node_code", "phase", "service_class", "entry"}
EXPERIMENT_KEYS = {"experiment_code", "external_jobs"}
EDGE_KEYS = {"from_node", "to_node"}
PROBE_KEYS = {"probe_code", "departures", "busy_s", "first_wave"}
HISTORY_KEYS = {"case_id", "fingerprint", "routing_sha256"}


class QueueInputError(ValueError):
    pass


def guard(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QueueInputError("duplicate JSON key")
        result[key] = value
    return result


def exact_keys(value, expected, label):
    if not isinstance(value, dict):
        raise QueueInputError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        raise QueueInputError(f"{label} missing key {missing[0]}")
    if extra:
        raise QueueInputError(f"{label} extra key {extra[0]}")


def text(value, label):
    if not isinstance(value, str) or not value or len(value) > 40:
        raise QueueInputError(f"{label} is not valid text")
    if "/" in value or "\\" in value:
        raise QueueInputError(f"{label} has a path separator")
    return value


def number(value, label, positive=False, integer=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise QueueInputError(f"{label} is not finite")
    if integer and int(value) != value:
        raise QueueInputError(f"{label} must be integral")
    if positive and float(value) <= 0:
        raise QueueInputError(f"{label} must be positive")
    return float(value)


def validate(payload):
    exact_keys(payload, TOP_KEYS, "payload")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise QueueInputError("schema_version must be integer 1")
    text(payload["case_id"], "case_id")
    candidates = payload["candidates"]
    experiments = payload["experiments"]
    edges = payload["edges"]
    probes = payload["probes"]
    if not all(isinstance(item, list) for item in (candidates, experiments, edges, probes)):
        raise QueueInputError("network arrays are required")
    if not 8 <= len(candidates) <= 12:
        raise QueueInputError("candidate count must be 8..12")
    if len(probes) != len(candidates) or len(experiments) != 6:
        raise QueueInputError("probe count must match candidates and six experiments are required")
    codes = set()
    entries = 0
    for index, item in enumerate(candidates):
        exact_keys(item, CANDIDATE_KEYS, f"candidates[{index}]")
        code = text(item["node_code"], "node_code")
        if code in codes:
            raise QueueInputError("node_code must be unique")
        codes.add(code)
        number(item["phase"], "phase", integer=True)
        if item["service_class"] not in {"quick", "normal", "slow"}:
            raise QueueInputError("service_class is invalid")
        if not isinstance(item["entry"], bool):
            raise QueueInputError("entry must be boolean")
        entries += int(item["entry"])
    if entries != 1:
        raise QueueInputError("exactly one candidate must be an entry")
    for index, item in enumerate(experiments):
        exact_keys(item, EXPERIMENT_KEYS, f"experiments[{index}]")
        text(item["experiment_code"], "experiment_code")
        number(item["external_jobs"], "external_jobs", True)
    for index, item in enumerate(edges):
        exact_keys(item, EDGE_KEYS, f"edges[{index}]")
        if item["from_node"] not in codes or item["to_node"] not in codes:
            raise QueueInputError("edge endpoint is unknown")
        if item["from_node"] == item["to_node"]:
            raise QueueInputError("self edges are not supported")
    probe_codes = set()
    for index, item in enumerate(probes):
        exact_keys(item, PROBE_KEYS, f"probes[{index}]")
        code = text(item["probe_code"], "probe_code")
        if code in probe_codes:
            raise QueueInputError("probe_code must be unique")
        probe_codes.add(code)
        if not isinstance(item["departures"], list) or not isinstance(item["busy_s"], list):
            raise QueueInputError("probe series must be arrays")
        if len(item["departures"]) != 6 or len(item["busy_s"]) != 6:
            raise QueueInputError("probe series must contain six values")
        for value in item["departures"]:
            number(value, "departures", positive=True)
        for value in item["busy_s"]:
            number(value, "busy_s", positive=True)
        number(item["first_wave"], "first_wave", integer=True)
    return payload


def read_input(path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise QueueInputError(f"cannot read input: {exc.strerror}") from exc
    if not raw.endswith(b"\n"):
        raise QueueInputError("input must end with a newline")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=guard)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueueInputError("input is not valid UTF-8 JSON") from exc
    return raw, validate(payload)


def rate_class(value):
    if value >= 2.9:
        return "quick"
    if value >= 1.7:
        return "normal"
    return "slow"


def hungarian(cost):
    n = len(cost)
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for row in range(1, n + 1):
        p[0] = row
        column0 = 0
        minv = [math.inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[column0] = True
            row0 = p[column0]
            delta = math.inf
            column1 = 0
            for column in range(1, n + 1):
                if used[column]:
                    continue
                current = cost[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minv[column]:
                    minv[column] = current
                    way[column] = column0
                if minv[column] < delta:
                    delta = minv[column]
                    column1 = column
            for column in range(n + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    minv[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    assignment = [0] * n
    for column in range(1, n + 1):
        if p[column]:
            assignment[p[column] - 1] = column - 1
    return assignment


def solve_least_squares(rows, values):
    width = len(rows[0])
    normal = [[0.0] * width for _ in range(width)]
    rhs = [0.0] * width
    for row, value in zip(rows, values):
        for left in range(width):
            rhs[left] += row[left] * value
            for right in range(width):
                normal[left][right] += row[left] * row[right]
    augmented = [normal[index] + [rhs[index]] for index in range(width)]
    for column in range(width):
        pivot = max(range(column, width), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise QueueInputError("routing system is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        for index in range(column, width + 1):
            augmented[column][index] /= scale
        for row in range(width):
            if row == column:
                continue
            factor = augmented[row][column]
            for index in range(column, width + 1):
                augmented[row][index] -= factor * augmented[column][index]
    return [augmented[index][width] for index in range(width)]


def calibrate(payload):
    probe_features = []
    for probe in payload["probes"]:
        aggregate = sum(probe["departures"]) / sum(probe["busy_s"])
        probe_features.append((probe["first_wave"], rate_class(aggregate)))
    class_rank = {"quick": 0, "normal": 1, "slow": 2}
    cost = []
    for wave, service in probe_features:
        row = []
        for candidate in payload["candidates"]:
            row.append(
                abs(wave - candidate["phase"]) * 4.0
                + abs(class_rank[service] - class_rank[candidate["service_class"]]) * 6.0
                + (0.0 if service == candidate["service_class"] else 10.0)
            )
        cost.append(row)
    assignment = hungarian(cost)
    node_for_probe = [payload["candidates"][index]["node_code"] for index in assignment]
    probe_for_node = {
        node_for_probe[index]: payload["probes"][index]["probe_code"]
        for index in range(len(node_for_probe))
    }
    node_probe = {
        node_for_probe[index]: payload["probes"][index]
        for index in range(len(node_for_probe))
    }
    throughput = {
        node: [value for value in node_probe[node]["departures"]]
        for node in node_probe
    }
    incoming = {}
    outgoing = {}
    for edge in payload["edges"]:
        incoming.setdefault(edge["to_node"], []).append(edge["from_node"])
        outgoing.setdefault(edge["from_node"], []).append(edge["to_node"])
    entry = next(item["node_code"] for item in payload["candidates"] if item["entry"])
    routing = {}
    for destination, sources in incoming.items():
        rows = []
        values = []
        for experiment in range(6):
            rows.append([throughput[source][experiment] for source in sources])
            values.append(throughput[destination][experiment])
        solution = solve_least_squares(rows, values)
        for source, probability in zip(sources, solution):
            routing[(source, destination)] = min(1.0, max(0.0, probability))
    routing_rows = []
    exit_rows = []
    nodes = [item["node_code"] for item in payload["candidates"]]
    for node in nodes:
        total = 0.0
        for destination in outgoing.get(node, []):
            probability = routing.get((node, destination), 0.0)
            total += probability
            if probability > 0.00005:
                routing_rows.append(
                    {
                        "from_node": node,
                        "to_node": destination,
                        "probability": round(probability, 6),
                    }
                )
        exit_rows.append({"node_code": node, "probability": round(max(0.0, 1.0 - total), 6)})
    routing_rows.sort(key=lambda row: (row["from_node"], row["to_node"]))
    exit_rows.sort(key=lambda row: row["node_code"])
    service_rows = []
    for node in nodes:
        probe = node_probe[node]
        service = sum(probe["departures"]) / sum(probe["busy_s"])
        service_rows.append({"node_code": node, "jobs_per_second": round(service, 6)})
    service_rows.sort(key=lambda row: row["node_code"])
    predicted = {node: 0.0 for node in nodes}
    predicted[entry] = sum(item["external_jobs"] for item in payload["experiments"])
    phase_by_node = {item["node_code"]: item["phase"] for item in payload["candidates"]}
    for node in sorted(nodes, key=lambda code: phase_by_node[code]):
        for row in routing_rows:
            if row["from_node"] == node:
                predicted[row["to_node"]] += predicted[node] * row["probability"]
    observed_total = sum(sum(probe["departures"]) for probe in payload["probes"])
    predicted_total = sum(predicted.values())
    node_map = [
        {"probe_code": probe_for_node[node], "node_code": node}
        for node in sorted(probe_for_node)
    ]
    return {
        "schema_version": 1,
        "case_id": payload["case_id"],
        "node_map": node_map,
        "routing": routing_rows,
        "exit_probabilities": exit_rows,
        "service_rates": service_rows,
        "simulation": {
            "predicted_total": round(predicted_total, 4),
            "observed_total": round(observed_total, 4),
            "relative_gap": round(abs(predicted_total - observed_total) / max(observed_total, 1e-9), 6),
        },
        "status": "calibrated",
    }


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def write_atomic(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".queue-", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_history(path):
    target = Path(path)
    if not target.exists():
        return []
    raw = target.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise QueueInputError("history must end with a newline")
    rows = []
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        try:
            row = json.loads(line, object_pairs_hook=guard)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QueueInputError(f"history line {number} is invalid") from exc
        exact_keys(row, HISTORY_KEYS, f"history line {number}")
        rows.append(row)
    return rows


def publish(payload, raw, history_path, report_path):
    report = calibrate(payload)
    fingerprint = hashlib.sha256(raw).hexdigest()
    history = read_history(history_path)
    if not any(row["case_id"] == payload["case_id"] and row["fingerprint"] == fingerprint for row in history):
        history.append(
            {
                "case_id": payload["case_id"],
                "fingerprint": fingerprint,
                "routing_sha256": canonical_hash(report["routing"]),
            }
        )
        write_atomic(history_path, b"".join(compact(row) for row in history))
    write_atomic(report_path, compact(report))


def compact(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--history", required=True)
    args = parser.parse_args(argv)
    old = Path(args.history).read_bytes() if Path(args.history).exists() else b""
    try:
        raw, payload = read_input(args.input)
        publish(payload, raw, args.history, args.report)
    except QueueInputError as exc:
        if Path(args.history).exists() and Path(args.history).read_bytes() != old:
            write_atomic(args.history, old)
        print(f"queue: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"queue: {exc.strerror}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
