# robot-qa

**로봇·자율주행 SW QA & 데이터 품질 검증 포트폴리오** — 장하린 ([@HALYNNN](https://github.com/HALYNNN))

Nav2 기반 자율주행 주행 시험을 **테스트 케이스 설계 → 로그 검증 → 결함 리포트**까지 한 흐름으로 정리한 레포지토리입니다.
시험 계획을 문서로만 두지 않고, 같은 기준을 코드로 반복 실행할 수 있게 만드는 데 초점을 두었습니다.

- 주행 시험: ROS2 Humble · Nav2 · Gazebo Classic · TurtleBot3 Waffle Pi
- 테스트 케이스 15건 (정상 4 · 경계 3 · 예외 8 / P1 11 · P2 4)
- 로그·데이터셋 검증 CLI 2종 + `pytest` 단위 테스트 26건
- 실무형 버그 리포트 템플릿

---

## 구성

| 경로 | 내용 |
|:---|:---|
| `test-cases/nav2-qa-testcases.md` | Nav2 자율주행 QA 테스트 케이스 15건 (사전 조건·실행 스텝·기대/실제 결과) |
| `test-cases/nav2-qa-testcases.csv` | 같은 내용의 CSV (엑셀에서 바로 열리도록 UTF-8 BOM) |
| `templates/bug-report-template.md` | 로봇·자율주행 SW QA 버그 리포트 템플릿 + 작성 기준 + 작성 예시 |
| `scripts/validate_nav2_log.py` | 주행 로그(JSONL)에서 타임스탬프 역전·Goal 실패·토픽 지연/드롭·비상정지 탐지 |
| `scripts/validate_label_dataset.py` | 라벨링 데이터셋(JSON)에서 이미지 누락·빈 어노테이션·bbox 좌표 이상·허용 외 클래스 검수 |
| `samples/sample_nav2_log.jsonl` | 두 스크립트를 바로 실행해 볼 수 있는 샘플 주행 로그 |
| `samples/sample_labels.json` | 샘플 라벨링 데이터셋 |
| `tests/test_validators.py` | 두 스크립트의 정상/오류 케이스 단위 테스트 (pytest · unittest 모두 지원) |
| `requirements.txt` | `pytest` (스크립트 본체는 표준 라이브러리만 사용) |

```
robot-qa/
├── README.md
├── requirements.txt
├── test-cases/
│   ├── nav2-qa-testcases.md
│   └── nav2-qa-testcases.csv
├── templates/
│   └── bug-report-template.md
├── scripts/
│   ├── validate_nav2_log.py
│   └── validate_label_dataset.py
├── samples/
│   ├── sample_nav2_log.jsonl
│   └── sample_labels.json
└── tests/
    └── test_validators.py
```

---

## 1. 테스트 케이스 (Nav2 자율주행 QA 15건)

`test-cases/nav2-qa-testcases.md` · `test-cases/nav2-qa-testcases.csv`

| TC ID | 모듈 | 유형 | 우선순위 | 판정 |
|:---|:---|:---|:---:|:---:|
| [NAV-001](test-cases/nav2-qa-testcases.md#nav001) | 주행 제어 (NavigateToPose) | 정상 | P1 | PASS |
| [NAV-002](test-cases/nav2-qa-testcases.md#nav002) | 경로계획 (Waypoint Follower) | 정상 | P1 | 미실행 |
| [NAV-003](test-cases/nav2-qa-testcases.md#nav003) | 경로계획·주행 (재탐색) | 정상 | P1 | 미실행 |
| [NAV-004](test-cases/nav2-qa-testcases.md#nav004) | 경로계획·주행 (Planner/Controller) | 경계 | P1 | **FAIL** |
| [NAV-005](test-cases/nav2-qa-testcases.md#nav005) | 위치추정 (AMCL) | 정상 | P1 | 미실행 |
| [NAV-006](test-cases/nav2-qa-testcases.md#nav006) | 주행 제어 (동역학 한계) | 경계 | P2 | 미실행 |
| [NAV-007](test-cases/nav2-qa-testcases.md#nav007) | Goal 처리 (tolerance) | 경계 | P2 | 미실행 |
| [NAV-008](test-cases/nav2-qa-testcases.md#nav008) | Goal 처리 (취소) | 예외 | P1 | 미실행 |
| [NAV-009](test-cases/nav2-qa-testcases.md#nav009) | Goal 처리 (덮어쓰기) | 예외 | P1 | 미실행 |
| [NAV-010](test-cases/nav2-qa-testcases.md#nav010) | 경로계획 (실패 처리) | 예외 | P1 | 미실행 |
| [NAV-011](test-cases/nav2-qa-testcases.md#nav011) | 동적 장애물 대응 | 예외 | P1 | 미실행 |
| [NAV-012](test-cases/nav2-qa-testcases.md#nav012) | 센서 (LiDAR) | 예외 | P1 | 미실행 |
| [NAV-013](test-cases/nav2-qa-testcases.md#nav013) | 위치추정 (odom) | 예외 | P2 | 미실행 |
| [NAV-014](test-cases/nav2-qa-testcases.md#nav014) | 초기화·맵 | 예외 | P1 | 미실행 |
| [NAV-015](test-cases/nav2-qa-testcases.md#nav015) | 로깅·성능 (rosbag2) | 예외 | P2 | 미실행 |

**설계 기준**

- 정상/경계/예외를 나눠 판정 기준값을 숫자로 명시했습니다(도착 오차 0.25 m, 경로 이탈 0.15 m, 정지 판정 0.01 m/s 등).
- 파라미터 값(`inflation_radius`, `goal_xy_tolerance`, `max_vel_x` 등)과 맵·goal 좌표를 사전 조건에 함께 적어 같은 조건을 그대로 재구성할 수 있게 했습니다.
- 실패했을 때 원인을 좁힐 수 있도록 "비교 시험"(예: NAV-004에서 `inflation_radius` 0.55 → 0.25)을 케이스에 포함했습니다.

> **결과 기록 범위**: 실제 결과가 채워진 2건(NAV-001 PASS, NAV-004 FAIL)은 시트에 **"(샘플 실행 기록)"** 으로 표기된 예시 기록을 그대로 옮긴 것이고, 나머지 13건은 시험 계획만 수립된 **미실행** 상태입니다. 측정하지 않은 수치는 넣지 않았습니다.

---

## 2. 주행 로그 검증 — `scripts/validate_nav2_log.py`

ROS2/Nav2 주행 로그를 JSONL(한 줄에 JSON 객체 하나)로 받아, 사람이 놓치기 쉬운 신호를 규칙으로 잡아냅니다.
rosbag2를 그대로 읽지 않고 JSONL을 입력으로 둔 이유는, 토픽·액션 상태·이벤트를 한 줄씩 남기면 규칙 검증과 회귀 비교가 쉬워지기 때문입니다.

### 탐지 규칙

| 코드 | 탐지 내용 | 기본 심각도 |
|:---|:---|:---:|
| `TIMESTAMP_INVERSION` | 로그 타임스탬프가 직전 레코드보다 작아짐 (역전) | error |
| `GOAL_FAILURE` | Goal 상태 `ABORTED` / `FAILED` | error |
| `TOPIC_DROP` | `actual_hz`가 기대 주기의 90% 미만이거나 `dropped` > 0 | error |
| `TOPIC_DELAY` | `delay_ms`가 200 ms 초과 (1000 ms 초과는 error) | warning |
| `EMERGENCY_STOP` | `active: true`인 비상정지 이벤트 | warning |
| `NODE_CRASH` | 노드 상태 `CRASHED` / `DIED` / `EXITED` / `SHUTDOWN` | error |
| `INVALID_JSON` | JSONL 한 줄 파싱 실패 | error |

### 로그 이벤트 스키마

| `event` | 필드 |
|:---|:---|
| `goal` / `goal_result` | `goal_id`, `status`(ACCEPTED·SUCCEEDED·ABORTED·FAILED·CANCELED), `reason`, `target` |
| `topic` | `topic`, `expected_hz`, `actual_hz`, `delay_ms`, `dropped` |
| `emergency_stop` | `source`, `reason`, `active` |
| `node` | `node`, `state` |
| 그 외(`run_start`, `run_end`, `log` …) | 자유 기술. `timestamp`만 있으면 시간 역전 검사에 포함 |

공통으로 `timestamp`(초, `ts`도 허용)를 씁니다.

### 실행

```bash
python3 scripts/validate_nav2_log.py samples/sample_nav2_log.jsonl
python3 scripts/validate_nav2_log.py run.jsonl --max-delay-ms 150 --min-hz-ratio 0.95
python3 scripts/validate_nav2_log.py run.jsonl --json --fail-on-warning
```

주요 옵션: `--max-delay-ms`(지연 경고), `--hard-delay-ms`(지연 오류), `--min-hz-ratio`(드롭 판정 비율), `--timestamp-tolerance`(역전 허용 오차), `--fail-on-warning`, `--json`

종료 코드: `0` 통과 · `1` 문제 발견 · `2` 입력/사용 오류 — CI에서 그대로 게이트로 쓸 수 있습니다.

### 실행 예 (샘플 로그)

```text
====================================================================
Nav2 / ROS2 주행 로그 검증 결과
====================================================================
파일      : samples/sample_nav2_log.jsonl
레코드 수 : 12
로그 구간 : 0.000s ~ 60.000s (약 60.0s)
Goal 상태 : ABORTED 1, ACCEPTED 2, SUCCEEDED 1
토픽 상태 :
  - /cmd_vel: 샘플 1, 평균 11.40 Hz (기대 10.00), 최대 지연 12 ms
  - /scan: 샘플 2, 평균 4.20 Hz (기대 5.00), 최대 지연 470 ms, 드롭 6건
--------------------------------------------------------------------
[WARNING] TOPIC_DELAY        (line   7, t=44.600s) /scan 수신 지연 470 ms (기준 200 ms / 한계 1000 ms)
[ERROR  ] TOPIC_DROP         (line   7, t=44.600s) /scan 수신 드롭: 3.40 Hz < 기대 5.00 Hz의 90%, 드롭 6건
[WARNING] EMERGENCY_STOP     (line   8, t=47.100s) 비상정지 발생 (source: collision_monitor): 통로 진입 전 정지 요구
[ERROR  ] TIMESTAMP_INVERSION (line   9, t=47.050s) 타임스탬프 역전: 47.100s → 47.050s (-0.050s, 직전 레코드 8행)
[ERROR  ] GOAL_FAILURE       (line  10, t=48.300s) Goal NAV-004-01 ABORTED: Failed to create plan with tolerance 0.50
--------------------------------------------------------------------
문제 유형 : EMERGENCY_STOP 1, GOAL_FAILURE 1, TIMESTAMP_INVERSION 1, TOPIC_DELAY 1, TOPIC_DROP 1
합계      : error 3 / warning 2
판정      : FAIL
====================================================================
```

---

## 3. 라벨링 데이터셋 검수 — `scripts/validate_label_dataset.py`

이미지·어노테이션 메타데이터 JSON을 검수합니다. 수작업 검수에서 반복적으로 나오는 오류 유형을 규칙으로 고정했습니다.

### 데이터셋 형식

```json
{
  "dataset": "sample_labels",
  "allowed_classes": ["car", "pedestrian", "traffic_light", "bicycle"],
  "images": [
    {
      "id": "img_0001",
      "path": "images/img_0001.jpg",
      "width": 1280,
      "height": 720,
      "annotations": [
        {"class": "car", "bbox": [120, 240, 300, 180]}
      ]
    }
  ]
}
```

`bbox`는 픽셀 단위 **`[x, y, width, height]`**, 좌상단 원점 기준입니다.

### 탐지 규칙

| 코드 | 탐지 내용 | 기본 심각도 |
|:---|:---|:---:|
| `IMAGE_MISSING` | 이미지 경로가 비어 있음, 또는 `--images-root` 기준 파일 없음 | error |
| `EMPTY_ANNOTATION` | `annotations` 키가 없거나 0건 | warning |
| `NEGATIVE_COORD` | `bbox`에 음수 값 | error |
| `INVALID_BBOX` | 숫자 4개가 아니거나 `w`/`h`가 0 이하 | error |
| `BBOX_OUT_OF_RANGE` | `x+w > width` 또는 `y+h > height` | error |
| `UNKNOWN_CLASS` | 허용 클래스 목록에 없는 클래스명 | error |
| `MISSING_CLASS` | 클래스명이 없음 | error |
| `DUPLICATE_IMAGE_ID` | 이미지 id 중복 | error |
| `MISSING_IMAGE_ID` | 이미지 id 누락 | error |
| `INVALID_IMAGE_SIZE` | `width`/`height`가 없거나 0 이하 | error |

### 실행

```bash
python3 scripts/validate_label_dataset.py samples/sample_labels.json
python3 scripts/validate_label_dataset.py labels.json --classes car,pedestrian,bus
python3 scripts/validate_label_dataset.py labels.json --images-root /data/images --json
```

`--images-root`를 주지 않으면 경로 문자열만 검사하고, 주면 실제 파일 존재까지 확인합니다.
`--classes`를 생략하면 JSON의 `allowed_classes`를 사용합니다.

### 실행 예 (샘플 데이터셋)

```text
====================================================================
라벨링 데이터셋 검수 결과
====================================================================
파일        : samples/sample_labels.json
데이터셋 이름: sample_labels
이미지 수   : 7
어노테이션  : 7건
빈 어노테이션 이미지: 1장
허용 클래스 : car, pedestrian, traffic_light, bicycle
--------------------------------------------------------------------
[WARNING] EMPTY_ANNOTATION   (img_0002, -) 어노테이션이 0건
[ERROR  ] NEGATIVE_COORD     (img_0003, ann#0) bbox에 음수 값이 있음 (y): [100, -20, 240, 160]
[ERROR  ] BBOX_OUT_OF_RANGE  (img_0004, ann#0) bbox가 이미지 범위를 벗어남: x+w=1420.0 (width 1280), y+h=600.0 (height 720)
[ERROR  ] UNKNOWN_CLASS      (img_0005, ann#0) 허용되지 않은 클래스: 'truck' (허용: bicycle, car, pedestrian, traffic_light)
[ERROR  ] IMAGE_MISSING      (img_0006, -) 이미지 경로가 비어 있음
[ERROR  ] INVALID_BBOX       (img_0007, ann#0) bbox가 숫자 4개([x, y, w, h])가 아님: '640,120,40,90'
--------------------------------------------------------------------
문제 유형 : BBOX_OUT_OF_RANGE 1, EMPTY_ANNOTATION 1, IMAGE_MISSING 1, INVALID_BBOX 1, NEGATIVE_COORD 1, UNKNOWN_CLASS 1
합계      : error 5 / warning 1
판정      : FAIL
====================================================================
```

---

## 4. 버그 리포트 템플릿 — `templates/bug-report-template.md`

주행 시험에서 나온 결함을 "다른 사람이 그대로 재현할 수 있는 형태"로 남기기 위한 양식입니다.

- 이슈 제목 규칙: `[모듈] 현상 + 재현 조건` (50자 이내)
- 심각도 S1~S4(영향 기준)와 우선순위 P1~P3(일정·현장 영향 기준)를 분리
- 테스트 환경에 OS·ROS2·시뮬레이터·파라미터 파일 커밋 해시까지 기록
- 재현 스텝은 명령어 단위 5단계 이내, 기대 결과에는 판정 기준값 포함
- rosbag2 경로·콘솔 로그·스크린샷·파라미터 파일을 첨부해 **첨부만으로 재현 가능**하게
- 등록 전 체크리스트와 NAV-004 FAIL 기준 작성 예시 포함

---

## 로컬 실행

```bash
git clone https://github.com/HALYNNN/robot-qa.git
cd robot-qa

pip install -r requirements.txt
pytest -q
```

`pytest` 없이 표준 라이브러리만으로도 동일한 테스트가 돌아갑니다.

```bash
python3 -m unittest discover -s tests -v
```

검증 스크립트 자체는 표준 라이브러리만 사용하므로 Python 3.9 이상이면 별도 설치 없이 실행됩니다.

```bash
python3 scripts/validate_nav2_log.py samples/sample_nav2_log.jsonl
python3 scripts/validate_label_dataset.py samples/sample_labels.json
```

---

## 참고

- `samples/`의 두 파일은 검증기 동작을 확인하기 위한 **합성 샘플**입니다. 실제 주행 로그가 아닙니다.
- 테스트 케이스의 실제 결과 2건은 원본 시트에 "(샘플 실행 기록)"으로 표기된 예시이며, 나머지 13건은 미실행입니다. 성과 수치는 측정된 값만 적었습니다.
- 다음 단계로 생각하고 있는 것: rosbag2 → JSONL 변환 어댑터, 검증 결과를 GitHub Actions에서 PR 코멘트로 남기는 워크플로.
