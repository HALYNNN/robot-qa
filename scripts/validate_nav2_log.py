#!/usr/bin/env python3
"""Nav2 / ROS2 주행 로그(JSONL) 검증기.

주행 로그를 JSONL(한 줄에 JSON 객체 하나)로 받아 다음 네 가지를 탐지한다.

1. 타임스탬프 역전 (TIMESTAMP_INVERSION)
2. Goal 실패·중단 (GOAL_FAILURE) — ABORTED / FAILED
3. 토픽 수신 지연·드롭 (TOPIC_DELAY / TOPIC_DROP)
4. 비상정지 이벤트 (EMERGENCY_STOP)

표준 라이브러리만 사용한다.

사용 예:
    python3 scripts/validate_nav2_log.py samples/sample_nav2_log.jsonl
    python3 scripts/validate_nav2_log.py run.jsonl --max-delay-ms 150 --json
    python3 scripts/validate_nav2_log.py run.jsonl --fail-on-warning

종료 코드: 0 통과, 1 문제 발견, 2 입력/사용 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ERROR = "error"
WARNING = "warning"

GOAL_FAILURE_STATUSES = {"ABORTED", "FAILED"}
NODE_CRASH_STATES = {"CRASHED", "DIED", "EXITED", "SHUTDOWN"}


@dataclass
class Config:
    """검증 기준값."""

    min_hz_ratio: float = 0.9
    soft_delay_ms: float = 200.0
    hard_delay_ms: float = 1000.0
    timestamp_tolerance_s: float = 0.0
    fail_on_warning: bool = False


@dataclass
class Finding:
    """탐지된 문제 한 건."""

    code: str
    severity: str
    line_no: int
    timestamp: Optional[float]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "line": self.line_no,
            "timestamp": self.timestamp,
            "message": self.message,
            "details": self.details,
        }


def _as_number(value: Any) -> Optional[float]:
    """숫자로 읽을 수 있는 값이면 float로 돌려주고, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _event_name(record: Dict[str, Any]) -> str:
    name = record.get("event", record.get("type", ""))
    return str(name).strip().lower() if name is not None else ""


def _timestamp(record: Dict[str, Any]) -> Optional[float]:
    return _as_number(record.get("timestamp", record.get("ts")))


def load_records(path: Path) -> Tuple[List[Tuple[int, Dict[str, Any]]], List[Finding]]:
    """JSONL 파일을 읽어 (행 번호, 레코드) 목록과 파싱 오류를 돌려준다.

    빈 줄과 `#`으로 시작하는 주석 줄은 건너뛴다.
    """
    records: List[Tuple[int, Dict[str, Any]]] = []
    findings: List[Finding] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                findings.append(
                    Finding(
                        code="INVALID_JSON",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=None,
                        message=f"JSON 파싱 실패: {exc.msg} (col {exc.colno})",
                        details={"line": line[:200]},
                    )
                )
                continue
            if not isinstance(parsed, dict):
                findings.append(
                    Finding(
                        code="INVALID_RECORD",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=None,
                        message="레코드가 JSON 객체가 아님",
                        details={"value_type": type(parsed).__name__},
                    )
                )
                continue
            records.append((line_no, parsed))
    return records, findings


def validate_records(
    records: Sequence[Tuple[int, Dict[str, Any]]],
    config: Optional[Config] = None,
    parse_findings: Optional[Iterable[Finding]] = None,
    source: str = "<memory>",
) -> Dict[str, Any]:
    """(행 번호, 레코드) 목록을 검증해 결과 딕셔너리를 돌려준다."""
    cfg = config or Config()
    findings: List[Finding] = list(parse_findings or [])

    prev_ts: Optional[float] = None
    prev_line: Optional[int] = None
    first_ts: Optional[float] = None
    last_ts: Optional[float] = None

    goal_status: Dict[str, int] = {}
    topics: Dict[str, Dict[str, Any]] = {}
    counts = {"emergency_stop": 0, "goal_failure": 0}

    for line_no, record in records:
        ts = _timestamp(record)
        if ts is not None:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            if prev_ts is not None and ts < prev_ts - cfg.timestamp_tolerance_s:
                findings.append(
                    Finding(
                        code="TIMESTAMP_INVERSION",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=ts,
                        message=(
                            f"타임스탬프 역전: {prev_ts:.3f}s → {ts:.3f}s "
                            f"({ts - prev_ts:+.3f}s, 직전 레코드 {prev_line}행)"
                        ),
                        details={"previous_timestamp": prev_ts, "previous_line": prev_line},
                    )
                )
            prev_ts = ts
            prev_line = line_no

        event = _event_name(record)

        if event in ("goal", "goal_result"):
            status = str(record.get("status", "")).strip().upper()
            goal_id = str(record.get("goal_id", record.get("id", "-")))
            if status:
                goal_status[status] = goal_status.get(status, 0) + 1
            if status in GOAL_FAILURE_STATUSES:
                counts["goal_failure"] += 1
                reason = str(record.get("reason", record.get("message", "")) or "").strip()
                detail_text = f": {reason}" if reason else ""
                findings.append(
                    Finding(
                        code="GOAL_FAILURE",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=ts,
                        message=f"Goal {goal_id} {status}{detail_text}",
                        details={
                            "goal_id": goal_id,
                            "status": status,
                            "reason": reason,
                            "target": record.get("target"),
                        },
                    )
                )

        elif event in ("topic", "topic_status"):
            topic = str(record.get("topic", "(unknown)"))
            expected_hz = _as_number(record.get("expected_hz"))
            actual_hz = _as_number(record.get("actual_hz", record.get("hz")))
            delay_ms = _as_number(record.get("delay_ms"))
            dropped = _as_number(record.get("dropped"))

            stats = topics.setdefault(
                topic,
                {
                    "samples": 0,
                    "expected_hz": expected_hz,
                    "hz_sum": 0.0,
                    "hz_count": 0,
                    "max_delay_ms": None,
                    "dropped": 0,
                },
            )
            stats["samples"] += 1
            if actual_hz is not None:
                stats["hz_sum"] += actual_hz
                stats["hz_count"] += 1
            if stats.get("expected_hz") is None and expected_hz is not None:
                stats["expected_hz"] = expected_hz
            if delay_ms is not None:
                current_max = stats["max_delay_ms"]
                stats["max_delay_ms"] = delay_ms if current_max is None else max(current_max, delay_ms)
            if dropped is not None:
                stats["dropped"] += int(dropped)

            low_hz = (
                actual_hz is not None
                and expected_hz is not None
                and expected_hz > 0
                and actual_hz < expected_hz * cfg.min_hz_ratio
            )
            has_drop = (dropped is not None and dropped > 0) or low_hz
            if has_drop:
                pieces = []
                if low_hz:
                    pieces.append(f"{actual_hz:.2f} Hz < 기대 {expected_hz:.2f} Hz의 {cfg.min_hz_ratio:.0%}")
                if dropped is not None and dropped > 0:
                    pieces.append(f"드롭 {int(dropped)}건")
                findings.append(
                    Finding(
                        code="TOPIC_DROP",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=ts,
                        message=f"{topic} 수신 드롭: " + ", ".join(pieces),
                        details={
                            "topic": topic,
                            "expected_hz": expected_hz,
                            "actual_hz": actual_hz,
                            "dropped": None if dropped is None else int(dropped),
                        },
                    )
                )

            if delay_ms is not None and delay_ms > cfg.soft_delay_ms:
                severity = ERROR if delay_ms > cfg.hard_delay_ms else WARNING
                findings.append(
                    Finding(
                        code="TOPIC_DELAY",
                        severity=severity,
                        line_no=line_no,
                        timestamp=ts,
                        message=(
                            f"{topic} 수신 지연 {delay_ms:.0f} ms "
                            f"(기준 {cfg.soft_delay_ms:.0f} ms / 한계 {cfg.hard_delay_ms:.0f} ms)"
                        ),
                        details={"topic": topic, "delay_ms": delay_ms},
                    )
                )

        elif event in ("emergency_stop", "estop", "e_stop"):
            if record.get("active", True) is False:
                continue
            counts["emergency_stop"] += 1
            stop_source = str(record.get("source", record.get("node", "-")))
            reason = str(record.get("reason", record.get("message", "")) or "").strip()
            findings.append(
                Finding(
                    code="EMERGENCY_STOP",
                    severity=WARNING,
                    line_no=line_no,
                    timestamp=ts,
                    message=f"비상정지 발생 (source: {stop_source})" + (f": {reason}" if reason else ""),
                    details={"source": stop_source, "reason": reason},
                )
            )

        elif event == "node":
            state = str(record.get("state", "")).strip().upper()
            if state in NODE_CRASH_STATES:
                node = str(record.get("node", record.get("name", "-")))
                findings.append(
                    Finding(
                        code="NODE_CRASH",
                        severity=ERROR,
                        line_no=line_no,
                        timestamp=ts,
                        message=f"{node} 상태 {state}",
                        details={"node": node, "state": state},
                    )
                )

    findings.sort(key=lambda finding: (finding.line_no, finding.code))

    errors = sum(1 for finding in findings if finding.severity == ERROR)
    warnings = sum(1 for finding in findings if finding.severity == WARNING)
    by_code: Dict[str, int] = {}
    for finding in findings:
        by_code[finding.code] = by_code.get(finding.code, 0) + 1

    failed = errors > 0 or (cfg.fail_on_warning and warnings > 0)

    topic_summary: Dict[str, Dict[str, Any]] = {}
    for name, stats in topics.items():
        mean_hz = stats["hz_sum"] / stats["hz_count"] if stats["hz_count"] else None
        topic_summary[name] = {
            "samples": stats["samples"],
            "expected_hz": stats["expected_hz"],
            "mean_hz": mean_hz,
            "max_delay_ms": stats["max_delay_ms"],
            "dropped": stats["dropped"],
        }

    return {
        "source": source,
        "records": len(records),
        "span": {"start": first_ts, "end": last_ts},
        "findings": [finding.as_dict() for finding in findings],
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "by_code": by_code,
            "goals": goal_status,
            "topics": topic_summary,
            "emergency_stops": counts["emergency_stop"],
            "goal_failures": counts["goal_failure"],
            "verdict": "FAIL" if failed else "PASS",
        },
    }


def run(path: Path, config: Optional[Config] = None) -> Dict[str, Any]:
    """파일 하나를 읽어 검증한 결과를 돌려준다."""
    records, parse_findings = load_records(path)
    return validate_records(
        records, config=config, parse_findings=parse_findings, source=str(path)
    )


def format_text(result: Dict[str, Any]) -> str:
    """사람이 읽기 좋은 텍스트 보고서."""
    summary = result["summary"]
    span = result["span"]
    lines: List[str] = []
    lines.append("=" * 68)
    lines.append("Nav2 / ROS2 주행 로그 검증 결과")
    lines.append("=" * 68)
    lines.append(f"파일      : {result['source']}")
    lines.append(f"레코드 수 : {result['records']}")

    if span["start"] is not None and span["end"] is not None:
        duration = span["end"] - span["start"]
        lines.append(f"로그 구간 : {span['start']:.3f}s ~ {span['end']:.3f}s (약 {duration:.1f}s)")

    goals = summary["goals"]
    if goals:
        goal_text = ", ".join(f"{status} {count}" for status, count in sorted(goals.items()))
        lines.append(f"Goal 상태 : {goal_text}")

    if summary["topics"]:
        lines.append("토픽 상태 :")
        for topic, stats in sorted(summary["topics"].items()):
            mean_hz = stats["mean_hz"]
            parts = [f"샘플 {stats['samples']}"]
            if mean_hz is not None:
                expected = stats["expected_hz"]
                if expected is not None:
                    parts.append(f"평균 {mean_hz:.2f} Hz (기대 {expected:.2f})")
                else:
                    parts.append(f"평균 {mean_hz:.2f} Hz")
            if stats["max_delay_ms"] is not None:
                parts.append(f"최대 지연 {stats['max_delay_ms']:.0f} ms")
            if stats["dropped"]:
                parts.append(f"드롭 {stats['dropped']}건")
            lines.append(f"  - {topic}: " + ", ".join(parts))

    lines.append("-" * 68)
    if not result["findings"]:
        lines.append("탐지된 문제 없음")
    else:
        for finding in result["findings"]:
            timestamp = finding["timestamp"]
            stamp = f"t={timestamp:.3f}s" if timestamp is not None else "t=-"
            lines.append(
                f"[{finding['severity'].upper():7}] {finding['code']:18} "
                f"(line {finding['line']:>3}, {stamp}) {finding['message']}"
            )
    lines.append("-" * 68)
    by_code = summary["by_code"]
    if by_code:
        lines.append(
            "문제 유형 : " + ", ".join(f"{code} {count}" for code, count in sorted(by_code.items()))
        )
    lines.append(f"합계      : error {summary['errors']} / warning {summary['warnings']}")
    lines.append(f"판정      : {summary['verdict']}")
    lines.append("=" * 68)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nav2/ROS2 주행 로그(JSONL)에서 타임스탬프 역전, Goal 실패, 토픽 지연·드롭, 비상정지를 탐지합니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("log", type=Path, help="검증할 JSONL 로그 파일")
    parser.add_argument(
        "--max-delay-ms",
        type=float,
        default=200.0,
        help="토픽 수신 지연 경고 기준(ms)",
    )
    parser.add_argument(
        "--hard-delay-ms",
        type=float,
        default=1000.0,
        help="토픽 수신 지연 오류 기준(ms)",
    )
    parser.add_argument(
        "--min-hz-ratio",
        type=float,
        default=0.9,
        help="기대 주기 대비 허용 최소 비율(이보다 낮으면 드롭)",
    )
    parser.add_argument(
        "--timestamp-tolerance",
        type=float,
        default=0.0,
        help="타임스탬프 역전으로 보기 전 허용 오차(초)",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="경고만 있어도 종료 코드 1로 실패 처리",
    )
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.log.is_file():
        print(f"입력 파일을 찾을 수 없습니다: {args.log}", file=sys.stderr)
        return 2

    if args.max_delay_ms < 0 or args.hard_delay_ms < args.max_delay_ms:
        print("--hard-delay-ms는 --max-delay-ms 이상이어야 합니다.", file=sys.stderr)
        return 2
    if not 0 < args.min_hz_ratio <= 1:
        print("--min-hz-ratio는 0보다 크고 1 이하여야 합니다.", file=sys.stderr)
        return 2

    config = Config(
        min_hz_ratio=args.min_hz_ratio,
        soft_delay_ms=args.max_delay_ms,
        hard_delay_ms=args.hard_delay_ms,
        timestamp_tolerance_s=args.timestamp_tolerance,
        fail_on_warning=args.fail_on_warning,
    )

    try:
        result = run(args.log, config)
    except OSError as exc:
        print(f"로그 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))

    return 0 if result["summary"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
