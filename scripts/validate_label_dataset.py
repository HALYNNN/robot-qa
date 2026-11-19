#!/usr/bin/env python3
"""라벨링 데이터셋(JSON) 검증기.

이미지·어노테이션 메타데이터를 담은 JSON을 읽어 다음을 검수한다.

1. 이미지 누락 (IMAGE_MISSING) — 경로가 비어 있거나, `--images-root` 기준으로 파일이 없음
2. 빈 어노테이션 (EMPTY_ANNOTATION)
3. 바운딩박스 좌표 이상 (INVALID_BBOX / NEGATIVE_COORD / BBOX_OUT_OF_RANGE)
4. 허용되지 않은 클래스명 (UNKNOWN_CLASS / MISSING_CLASS)

표준 라이브러리만 사용한다.

데이터셋 형식(요약):
{
  "dataset": "sample_labels",
  "allowed_classes": ["car", "pedestrian"],
  "images": [
    {"id": "img_0001", "path": "images/img_0001.jpg", "width": 1280, "height": 720,
     "annotations": [{"class": "car", "bbox": [x, y, w, h]}]}
  ]
}
`bbox`는 픽셀 단위 [x, y, width, height]이고 좌상단 원점 기준이다.

사용 예:
    python3 scripts/validate_label_dataset.py samples/sample_labels.json
    python3 scripts/validate_label_dataset.py labels.json --images-root /data/images --classes car,bus
    python3 scripts/validate_label_dataset.py labels.json --json

종료 코드: 0 통과, 1 문제 발견, 2 입력/사용 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ERROR = "error"
WARNING = "warning"

COORD_EPSILON = 1e-6


@dataclass
class Config:
    """검증 기준값."""

    allowed_classes: Optional[Sequence[str]] = None
    images_root: Optional[Path] = None
    fail_on_warning: bool = False


@dataclass
class Finding:
    """탐지된 문제 한 건."""

    code: str
    severity: str
    image_id: str
    annotation_index: Optional[int]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "image_id": self.image_id,
            "annotation_index": self.annotation_index,
            "message": self.message,
            "details": self.details,
        }


def _as_number(value: Any) -> Optional[float]:
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


def _load_images(data: Any) -> List[Any]:
    """데이터셋 JSON에서 이미지 목록을 꺼낸다. 최상위가 배열이면 그대로 쓴다."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        images = data.get("images")
        if isinstance(images, list):
            return images
    raise ValueError("데이터셋에 'images' 배열이 없습니다.")


def _dataset_classes(data: Any) -> Optional[List[str]]:
    if not isinstance(data, dict):
        return None
    for key in ("allowed_classes", "classes", "class_names"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return [str(item).strip() for item in value]
    return None


def validate_dataset(data: Any, config: Optional[Config] = None, source: str = "<memory>") -> Dict[str, Any]:
    """데이터셋 객체를 검증해 결과 딕셔너리를 돌려준다."""
    cfg = config or Config()
    images = _load_images(data)
    allowed = list(cfg.allowed_classes) if cfg.allowed_classes else _dataset_classes(data)
    allowed_set = set(allowed) if allowed else None

    findings: List[Finding] = []
    seen_ids = set()
    annotation_total = 0
    empty_images = 0

    for image_index, image in enumerate(images):
        if not isinstance(image, dict):
            findings.append(
                Finding(
                    code="INVALID_IMAGE_RECORD",
                    severity=ERROR,
                    image_id=f"<index {image_index}>",
                    annotation_index=None,
                    message="이미지 항목이 JSON 객체가 아님",
                    details={"value_type": type(image).__name__},
                )
            )
            continue

        image_id_raw = image.get("id", image.get("image_id"))
        image_id = str(image_id_raw).strip() if image_id_raw not in (None, "") else ""
        label_for_report = image_id or f"<index {image_index}>"

        if not image_id:
            findings.append(
                Finding(
                    code="MISSING_IMAGE_ID",
                    severity=ERROR,
                    image_id=label_for_report,
                    annotation_index=None,
                    message="이미지 id가 없음",
                    details={"index": image_index},
                )
            )
        elif image_id in seen_ids:
            findings.append(
                Finding(
                    code="DUPLICATE_IMAGE_ID",
                    severity=ERROR,
                    image_id=image_id,
                    annotation_index=None,
                    message=f"이미지 id 중복: {image_id}",
                    details={"index": image_index},
                )
            )
        else:
            seen_ids.add(image_id)

        path_value = image.get("path", image.get("file"))
        path_text = path_value.strip() if isinstance(path_value, str) else ""
        if not path_text:
            findings.append(
                Finding(
                    code="IMAGE_MISSING",
                    severity=ERROR,
                    image_id=label_for_report,
                    annotation_index=None,
                    message="이미지 경로가 비어 있음",
                    details={"path": path_value},
                )
            )
        elif cfg.images_root is not None:
            resolved = Path(path_text)
            if not resolved.is_absolute():
                resolved = cfg.images_root / resolved
            if not resolved.is_file():
                findings.append(
                    Finding(
                        code="IMAGE_MISSING",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=None,
                        message=f"이미지 파일을 찾을 수 없음: {path_text}",
                        details={"path": path_text, "resolved": str(resolved)},
                    )
                )

        width = _as_number(image.get("width"))
        height = _as_number(image.get("height"))
        size_ok = width is not None and height is not None and width > 0 and height > 0
        if not size_ok:
            findings.append(
                Finding(
                    code="INVALID_IMAGE_SIZE",
                    severity=ERROR,
                    image_id=label_for_report,
                    annotation_index=None,
                    message=f"이미지 크기 값이 올바르지 않음 (width={image.get('width')}, height={image.get('height')})",
                    details={"width": image.get("width"), "height": image.get("height")},
                )
            )

        annotations = image.get("annotations")
        if annotations is None:
            empty_images += 1
            findings.append(
                Finding(
                    code="EMPTY_ANNOTATION",
                    severity=WARNING,
                    image_id=label_for_report,
                    annotation_index=None,
                    message="annotations 키가 없음",
                    details={"index": image_index},
                )
            )
            continue
        if not isinstance(annotations, list):
            findings.append(
                Finding(
                    code="INVALID_ANNOTATIONS",
                    severity=ERROR,
                    image_id=label_for_report,
                    annotation_index=None,
                    message=f"annotations가 배열이 아님 ({type(annotations).__name__})",
                    details={"index": image_index},
                )
            )
            continue
        if not annotations:
            empty_images += 1
            findings.append(
                Finding(
                    code="EMPTY_ANNOTATION",
                    severity=WARNING,
                    image_id=label_for_report,
                    annotation_index=None,
                    message="어노테이션이 0건",
                    details={"index": image_index},
                )
            )
            continue

        for annotation_index, annotation in enumerate(annotations):
            annotation_total += 1
            if not isinstance(annotation, dict):
                findings.append(
                    Finding(
                        code="INVALID_ANNOTATION",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message="어노테이션 항목이 JSON 객체가 아님",
                        details={"value_type": type(annotation).__name__},
                    )
                )
                continue

            class_name_raw = annotation.get("class", annotation.get("label"))
            class_name = str(class_name_raw).strip() if class_name_raw not in (None, "") else ""
            if not class_name:
                findings.append(
                    Finding(
                        code="MISSING_CLASS",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message="클래스명이 없음",
                        details={},
                    )
                )
            elif allowed_set is not None and class_name not in allowed_set:
                findings.append(
                    Finding(
                        code="UNKNOWN_CLASS",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message=f"허용되지 않은 클래스: '{class_name}' (허용: {', '.join(sorted(allowed_set))})",
                        details={"class": class_name, "allowed": sorted(allowed_set)},
                    )
                )

            bbox = annotation.get("bbox")
            coordinates = None
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                coordinates = [_as_number(value) for value in bbox]
            if coordinates is None or any(value is None for value in coordinates):
                findings.append(
                    Finding(
                        code="INVALID_BBOX",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message=f"bbox가 숫자 4개([x, y, w, h])가 아님: {bbox!r}",
                        details={"bbox": bbox if isinstance(bbox, (list, tuple)) else str(bbox)},
                    )
                )
                continue

            x, y, box_width, box_height = coordinates  # type: ignore[misc]
            assert x is not None and y is not None and box_width is not None and box_height is not None

            negative = [name for name, value in (("x", x), ("y", y), ("w", box_width), ("h", box_height)) if value < 0]
            if negative:
                findings.append(
                    Finding(
                        code="NEGATIVE_COORD",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message=f"bbox에 음수 값이 있음 ({', '.join(negative)}): {bbox!r}",
                        details={"bbox": [x, y, box_width, box_height]},
                    )
                )
                continue

            if box_width <= 0 or box_height <= 0:
                findings.append(
                    Finding(
                        code="INVALID_BBOX",
                        severity=ERROR,
                        image_id=label_for_report,
                        annotation_index=annotation_index,
                        message=f"bbox 크기가 0 이하 (w={box_width}, h={box_height})",
                        details={"bbox": [x, y, box_width, box_height]},
                    )
                )
                continue

            if size_ok:
                assert width is not None and height is not None
                over_width = x + box_width - width
                over_height = y + box_height - height
                if over_width > COORD_EPSILON or over_height > COORD_EPSILON:
                    findings.append(
                        Finding(
                            code="BBOX_OUT_OF_RANGE",
                            severity=ERROR,
                            image_id=label_for_report,
                            annotation_index=annotation_index,
                            message=(
                                f"bbox가 이미지 범위를 벗어남: "
                                f"x+w={x + box_width:.1f} (width {width:.0f}), "
                                f"y+h={y + box_height:.1f} (height {height:.0f})"
                            ),
                            details={
                                "bbox": [x, y, box_width, box_height],
                                "image_width": width,
                                "image_height": height,
                            },
                        )
                    )

    errors = sum(1 for finding in findings if finding.severity == ERROR)
    warnings = sum(1 for finding in findings if finding.severity == WARNING)
    by_code: Dict[str, int] = {}
    for finding in findings:
        by_code[finding.code] = by_code.get(finding.code, 0) + 1

    failed = errors > 0 or (cfg.fail_on_warning and warnings > 0)

    return {
        "source": source,
        "dataset": data.get("dataset") if isinstance(data, dict) else None,
        "images": len(images),
        "annotations": annotation_total,
        "empty_annotation_images": empty_images,
        "allowed_classes": allowed,
        "findings": [finding.as_dict() for finding in findings],
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "by_code": by_code,
            "verdict": "FAIL" if failed else "PASS",
        },
    }


def run(path: Path, config: Optional[Config] = None) -> Dict[str, Any]:
    """데이터셋 JSON 파일을 읽어 검증한다."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return validate_dataset(data, config=config, source=str(path))


def format_text(result: Dict[str, Any]) -> str:
    """사람이 읽기 좋은 텍스트 보고서."""
    summary = result["summary"]
    lines: List[str] = []
    lines.append("=" * 68)
    lines.append("라벨링 데이터셋 검수 결과")
    lines.append("=" * 68)
    lines.append(f"파일        : {result['source']}")
    if result.get("dataset"):
        lines.append(f"데이터셋 이름: {result['dataset']}")
    lines.append(f"이미지 수   : {result['images']}")
    lines.append(f"어노테이션  : {result['annotations']}건")
    lines.append(f"빈 어노테이션 이미지: {result['empty_annotation_images']}장")
    if result["allowed_classes"]:
        lines.append("허용 클래스 : " + ", ".join(result["allowed_classes"]))
    else:
        lines.append("허용 클래스 : (미지정 — 클래스명 검사 생략)")

    lines.append("-" * 68)
    if not result["findings"]:
        lines.append("탐지된 문제 없음")
    else:
        for finding in result["findings"]:
            index = finding["annotation_index"]
            where = f"ann#{index}" if index is not None else "-"
            lines.append(
                f"[{finding['severity'].upper():7}] {finding['code']:18} "
                f"({finding['image_id']}, {where}) {finding['message']}"
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
        description="라벨링 데이터셋(JSON)에서 이미지 누락, 빈 어노테이션, bbox 좌표 이상, 허용 외 클래스를 검수합니다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("dataset", type=Path, help="검수할 데이터셋 JSON 파일")
    parser.add_argument(
        "--classes",
        default=None,
        help="허용 클래스 목록(쉼표 구분). 생략하면 JSON의 allowed_classes를 사용",
    )
    parser.add_argument(
        "--images-root",
        type=Path,
        default=None,
        help="이미지 파일 존재 여부를 확인할 루트 디렉토리(생략하면 경로 문자열만 검사)",
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

    if not args.dataset.is_file():
        print(f"입력 파일을 찾을 수 없습니다: {args.dataset}", file=sys.stderr)
        return 2
    if args.images_root is not None and not args.images_root.is_dir():
        print(f"이미지 루트 디렉토리를 찾을 수 없습니다: {args.images_root}", file=sys.stderr)
        return 2

    allowed: Optional[List[str]] = None
    if args.classes:
        allowed = [item.strip() for item in args.classes.split(",") if item.strip()]

    config = Config(
        allowed_classes=allowed,
        images_root=args.images_root,
        fail_on_warning=args.fail_on_warning,
    )

    try:
        result = run(args.dataset, config)
    except json.JSONDecodeError as exc:
        print(f"JSON 파싱 실패: {exc.msg} (line {exc.lineno}, col {exc.colno})", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"데이터셋 형식 오류: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"데이터셋 파일을 읽지 못했습니다: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))

    return 0 if result["summary"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
