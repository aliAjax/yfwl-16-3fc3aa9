from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest


ENTRY = os.environ.get("QUEUE_ENTRY", "/app/queue_network.py")
DATA_DIR = Path(os.environ.get("QUEUE_DATA_DIR", "/app/data"))
MAIN_CASE = DATA_DIR / "queue_case.json"
GOLDEN = Path(__file__).with_name("golden.json")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def invoke(tmp_path, payload, name="payload.json", history=None):
    case = tmp_path / name
    case.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    state = history if history is not None else tmp_path / "history.jsonl"
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(report), "--history", str(state)],
        text=True,
        capture_output=True,
        timeout=15,
    )
    return process, report, state


def main_payload():
    return load_json(MAIN_CASE)


def invalid(tmp_path, payload):
    process, report, history = invoke(tmp_path, payload)
    return process, report, history


def test_main_queue_gate(tmp_path):
    """题面：「分拣中心换线后，十台计数器被重新装到输送网的不同节点，标签顺序没有更新。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    data = load_json(report)
    golden = load_json(GOLDEN)
    assert process.returncode == 0
    assert data["node_map"] == golden["node_map"]


def test_success_channels_are_empty(tmp_path):
    """题面：「换线校准完成后不产生标准输出或标准错误文本。」"""
    process, _, _ = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert process.stdout == ""
    assert process.stderr == ""


def test_report_keys_are_exact(tmp_path):
    """题面：「快照根成员是 `schema_version`、`case_id`、`candidates`、`experiments`、`edges`、`probes`。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert set(load_json(report)) == {
        "schema_version", "case_id", "node_map", "routing", "exit_probabilities",
        "service_rates", "simulation", "status",
    }


def test_node_map_keys_are_exact(tmp_path):
    """题面：「`node_map` 元素键集合为 `probe_code`、`node_code`，按 `node_code` 排序。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    rows = load_json(report)["node_map"]
    assert process.returncode == 0
    assert all(set(row) == {"probe_code", "node_code"} for row in rows)


def test_routing_keys_are_exact(tmp_path):
    """题面：「`routing` 元素键集合为 `from_node`、`to_node`、`probability`，按起终点字典序排列。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    rows = load_json(report)["routing"]
    assert process.returncode == 0
    assert all(set(row) == {"from_node", "to_node", "probability"} for row in rows)


def test_exit_keys_are_exact(tmp_path):
    """题面：「`routing` 元素键集合为 `from_node`、`to_node`、`probability`，按起终点字典序排列。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    rows = load_json(report)["exit_probabilities"]
    assert process.returncode == 0
    assert all(set(row) == {"node_code", "probability"} for row in rows)


def test_service_keys_are_exact(tmp_path):
    """题面：「`service_rates` 元素键集合为 `node_code`、`jobs_per_second`。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    rows = load_json(report)["service_rates"]
    assert process.returncode == 0
    assert all(set(row) == {"node_code", "jobs_per_second"} for row in rows)


def test_simulation_keys_are_exact(tmp_path):
    """题面：「`simulation` 键集合为 `predicted_total`、`observed_total`、`relative_gap`。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert set(load_json(report)["simulation"]) == {"predicted_total", "observed_total", "relative_gap"}


def test_node_map_is_sorted(tmp_path):
    """题面：「`node_map` 元素键集合为 `probe_code`、`node_code`，按 `node_code` 排序。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    codes = [row["node_code"] for row in load_json(report)["node_map"]]
    assert process.returncode == 0
    assert codes == sorted(codes)


def test_mapping_matches_wave_and_service_grade(tmp_path):
    """题面：「映射必须是一一对应，不能把两台计数器归到同一个节点。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = {row["probe_code"]: row["node_code"] for row in load_json(report)["node_map"]}
    assert process.returncode == 0
    assert actual == load_json(GOLDEN)["mapping"]


def test_routing_probabilities_match_truth(tmp_path):
    """题面：「已知边关系后，用六次运行中相邻节点的离开量解出分流概率。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = {
        f'{row["from_node"]}|{row["to_node"]}': row["probability"]
        for row in load_json(report)["routing"]
    }
    expected = load_json(GOLDEN)["routing"]
    assert process.returncode == 0
    assert all(abs(actual[key] - value) < 0.002 for key, value in expected.items())


def test_exit_and_edges_sum_to_one(tmp_path):
    """题面：「每个源节点的边概率加上出口概率等于一。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    data = load_json(report)
    outgoing = {}
    for row in data["routing"]:
        outgoing[row["from_node"]] = outgoing.get(row["from_node"], 0.0) + row["probability"]
    exits = {row["node_code"]: row["probability"] for row in data["exit_probabilities"]}
    assert process.returncode == 0
    assert all(abs(outgoing.get(node, 0.0) + exits[node] - 1.0) < 0.002 for node in exits)


def test_service_rates_match_truth(tmp_path):
    """题面：「计数器归属由首波层级和服务等级共同确定，候选的服务率则从离开量与占用时间的比值恢复。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    actual = {row["node_code"]: row["jobs_per_second"] for row in load_json(report)["service_rates"]}
    expected = load_json(GOLDEN)["service_rates"]
    assert process.returncode == 0
    assert all(abs(actual[node] - value) < 0.02 for node, value in expected.items())


def test_service_rate_uses_all_departures_over_all_busy_seconds(tmp_path):
    """题面锁定服务率为六次离开量之和除以六次占用时间之和。"""
    payload = main_payload()
    process, report, _ = invoke(tmp_path, payload)
    data = load_json(report)
    assert process.returncode == 0
    probes = {item["probe_code"]: item for item in payload["probes"]}
    probe_by_node = {
        item["node_code"]: item["probe_code"] for item in data["node_map"]
    }
    service = {
        item["node_code"]: item["jobs_per_second"]
        for item in data["service_rates"]
    }
    for node, probe_code in probe_by_node.items():
        probe = probes[probe_code]
        expected = sum(probe["departures"]) / sum(probe["busy_s"])
        assert service[node] == pytest.approx(round(expected, 6), abs=1e-6)


def test_deterministic_throughput_gap_is_small(tmp_path):
    """题面：「仿真从唯一入口注入累计外部作业量，按刚恢复的路由做确定性通量传播，并比较总离开量。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    simulation = load_json(report)["simulation"]
    assert process.returncode == 0
    assert simulation["relative_gap"] < 0.01


def test_case_id_is_preserved(tmp_path):
    """题面：「报告键集合为 `schema_version`、`case_id`、`node_map`、`routing`、`exit_probabilities`、`service_rates`、`simulation`、`status`。」"""
    payload = main_payload()
    payload["case_id"] = "night-shift-314"
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 0
    assert load_json(report)["case_id"] == "night-shift-314"


def test_minimum_node_count_is_accepted(tmp_path):
    """题面：「候选节点数为 8 到 12，试运行固定为 6 次，计数器数与候选节点数相同。」"""
    payload = main_payload()
    for _ in range(2):
        payload["candidates"].pop()
        payload["probes"].pop()
    payload["edges"] = [
        edge for edge in payload["edges"]
        if edge["from_node"] in {row["node_code"] for row in payload["candidates"]}
        and edge["to_node"] in {row["node_code"] for row in payload["candidates"]}
    ]
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 0
    assert len(load_json(report)["node_map"]) == 8


def test_too_few_nodes_are_rejected(tmp_path):
    """题面：「候选节点数为 8 到 12，试运行固定为 6 次，计数器数与候选节点数相同。」"""
    payload = main_payload()
    for _ in range(3):
        payload["candidates"].pop()
        payload["probes"].pop()
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_upper_node_count_is_accepted(tmp_path):
    """题面：「计数器清单最多容纳十二台；第十三台触发范围拒收。」"""
    payload = main_payload()
    for index in range(2):
        candidate = dict(payload["candidates"][index])
        candidate["node_code"] = f"UP-{index + 1:02d}"
        candidate["entry"] = False
        probe = dict(payload["probes"][index])
        probe["probe_code"] = f"UPP-{index + 1:02d}"
        payload["candidates"].append(candidate)
        payload["probes"].append(probe)
    process, report, _ = invoke(tmp_path, payload)
    assert process.returncode == 0
    assert len(load_json(report)["node_map"]) == 12


def test_too_many_nodes_are_rejected(tmp_path):
    """题面：「计数器清单最多容纳十二台；第十三台触发范围拒收。」"""
    payload = main_payload()
    for index in range(3):
        candidate = dict(payload["candidates"][index])
        candidate["node_code"] = f"OVER-{index + 1:02d}"
        candidate["entry"] = False
        probe = dict(payload["probes"][index])
        probe["probe_code"] = f"OP-{index + 1:02d}"
        payload["candidates"].append(candidate)
        payload["probes"].append(probe)
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_duplicate_probe_code_is_rejected(tmp_path):
    """题面：「文件版本仅允许数值一；布尔值属于非法输入。」"""
    payload = main_payload()
    payload["probes"][1]["probe_code"] = payload["probes"][0]["probe_code"]
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_missing_nested_key_is_rejected(tmp_path):
    """题面：「键集合不完整或夹带未声明条目时，本批数据不得进入求解。」"""
    payload = main_payload()
    del payload["candidates"][0]["phase"]
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_extra_nested_key_is_rejected(tmp_path):
    """题面：「键集合不完整或夹带未声明条目时，本批数据不得进入求解。」"""
    payload = main_payload()
    payload["experiments"][0]["shift_note"] = "night"
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_duplicate_top_key_is_rejected(tmp_path):
    """题面：「若同一对象内同名键再次出现，流程按规定停机且不发布报告。」"""
    text = MAIN_CASE.read_text(encoding="utf-8")
    case = tmp_path / "payload.json"
    case.write_text(text.replace('"schema_version":1}', '"schema_version":1,"schema_version":1}', 1), encoding="utf-8")
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(tmp_path / "report.json"), "--history", str(tmp_path / "history.jsonl")],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not (tmp_path / "report.json").exists()


def test_duplicate_nested_key_is_rejected(tmp_path):
    """题面：「快照根成员是 `schema_version`、`case_id`、`candidates`、`experiments`、`edges`、`probes`。」"""
    text = MAIN_CASE.read_text(encoding="utf-8")
    case = tmp_path / "payload.json"
    case.write_text(text.replace('"phase":0', '"phase":0,"phase":0', 1), encoding="utf-8")
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(tmp_path / "report.json"), "--history", str(tmp_path / "history.jsonl")],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not (tmp_path / "report.json").exists()


def test_boolean_schema_version_is_rejected(tmp_path):
    """题面：「文件版本仅允许数值一；布尔值属于非法输入。」"""
    payload = main_payload()
    payload["schema_version"] = True
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_nonfinite_departure_is_rejected(tmp_path):
    """题面：「拒收时只允许在标准错误出现一行以 `queue: ` 开头的说明，状态码二，调用栈不落盘。」"""
    payload = main_payload()
    payload["probes"][0]["departures"][2] = float("inf")
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_service_class_is_enumerated(tmp_path):
    """题面：「拒收时只允许在标准错误出现一行以 `queue: ` 开头的说明，状态码二，调用栈不落盘。」"""
    payload = main_payload()
    payload["candidates"][0]["service_class"] = "turbo"
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_exactly_one_entry_is_required(tmp_path):
    """题面：「`entry` 是布尔值，并且只有一个节点为真。」"""
    payload = main_payload()
    payload["candidates"][1]["entry"] = True
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_self_edge_is_rejected(tmp_path):
    """题面：「边的端点存在于候选节点集合，且不允许自环。」"""
    payload = main_payload()
    payload["edges"][0]["to_node"] = payload["edges"][0]["from_node"]
    process, report, _ = invalid(tmp_path, payload)
    assert process.returncode == 2
    assert process.stderr.startswith("queue: ")
    assert not report.exists()


def test_history_keys_are_exact(tmp_path):
    """题面：「`--history` 是追加式校准历史，每行键集合为 `case_id`、`fingerprint`、`routing_sha256`。」"""
    process, _, history = invoke(tmp_path, main_payload())
    row = json.loads(history.read_text(encoding="utf-8"))
    assert process.returncode == 0
    assert set(row) == {"case_id", "fingerprint", "routing_sha256"}


def test_first_run_appends_one_record(tmp_path):
    """题面：「首次输入追加一条。」"""
    process, _, history = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert len(history.read_text(encoding="utf-8").splitlines()) == 1


def test_repeated_input_is_idempotent(tmp_path):
    """题面：「同一原始文件的再次校准不得改动历史文件。」"""
    first, _, history = invoke(tmp_path, main_payload(), "first.json")
    before = history.read_bytes()
    second, _, _ = invoke(tmp_path, main_payload(), "second.json", history)
    after = history.read_bytes()
    assert first.returncode == 0
    assert second.returncode == 0
    assert after == before


def test_changed_input_appends_record(tmp_path):
    """题面：「同案号换输入追加新记录。」"""
    first, _, history = invoke(tmp_path, main_payload(), "first.json")
    payload = main_payload()
    payload["experiments"][0]["external_jobs"] += 1
    second, _, _ = invoke(tmp_path, payload, "second.json", history)
    assert first.returncode == 0
    assert second.returncode == 0
    assert len(history.read_text(encoding="utf-8").splitlines()) == 2


def test_failed_input_preserves_history(tmp_path):
    """题面：「失败输入保持旧字节。」"""
    first, _, history = invoke(tmp_path, main_payload())
    before = history.read_bytes()
    payload = main_payload()
    payload["schema_version"] = 2
    second, _, _ = invoke(tmp_path, payload, "second.json", history)
    assert first.returncode == 0
    assert second.returncode == 2
    assert history.read_bytes() == before


def test_history_fingerprint_is_raw_sha256(tmp_path):
    """题面：「`--history` 是追加式校准历史，每行键集合为 `case_id`、`fingerprint`、`routing_sha256`。」"""
    payload = main_payload()
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    process, _, history = invoke(tmp_path, payload)
    row = json.loads(history.read_text(encoding="utf-8"))
    assert process.returncode == 0
    assert row["fingerprint"] == hashlib.sha256(raw).hexdigest()


def test_report_and_history_are_compact(tmp_path):
    """题面：「写入采用同目录临时文件再切换目标文件。」"""
    process, report, history = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert b'": ' not in report.read_bytes()
    assert b'": ' not in history.read_bytes()
    assert report.read_bytes().endswith(b"\n")
    assert history.read_bytes().endswith(b"\n")


def test_status_is_calibrated(tmp_path):
    """题面：「报告键集合为 `schema_version`、`case_id`、`node_map`、`routing`、`exit_probabilities`、`service_rates`、`simulation`、`status`。」"""
    process, report, _ = invoke(tmp_path, main_payload())
    assert process.returncode == 0
    assert load_json(report)["status"] == "calibrated"


def test_run_finishes_below_public_limit(tmp_path):
    """题面：「一次换线校准的预算为十二秒；看门狗在十五秒时强制结束。」"""
    case = tmp_path / "payload.json"
    case.write_text(
        json.dumps(main_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    process = subprocess.run(
        ["python3", ENTRY, "--input", str(case), "--report", str(tmp_path / "report.json"), "--history", str(tmp_path / "history.jsonl")],
        text=True,
        capture_output=True,
        timeout=15,
    )
    elapsed = time.monotonic() - started
    assert process.returncode == 0
    assert elapsed < 12
