[English](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.md) | [繁體中文](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.zh-TW.md) | [日本語](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.ja.md) | [한국어](https://github.com/ez2app/ez2-aib/blob/main/docs/AIB-SHIP-FOR-USERS.ko.md)

# 푸시할 때 aib가 절약해 주는 시간: 사용자 안내서

한 문장으로 요약하면 **푸시하기 전마다 aib는 이번 변경이 실제로 영향을 줄 수 있는 테스트만 다시 실행하고 나머지는 지난 결과를 재사용합니다. 그리고 매번 이번 푸시로 얼마나 절약했는지, 사용을 시작한 이후 총 얼마나 절약했는지 알려 줍니다.**

(명령의 자세한 내용은 `AIB-TEST-QUICKSTART.ko.md`의 6절에 있습니다. 이 안내서는 무엇이 보이는지, 어떻게 읽는지, 어떻게 하면 가장 많이 절약하는지만 다룹니다.)

---

## 1. 시작하기(약 5분, 그리고 모든 테스트를 한 번 실행하는 시간)

```bash
cd ~/your-project
aib ship init
```

다음을 순서대로 수행하며, **푸시나 커밋은 하지 않습니다**:
1. 프로젝트 이름을 보여 주고, 버전이 어디에 있는지 찾고, 테스트를 찾습니다.
2. 테스트가 **이 폴더의 코드**를 테스트하는지 확인합니다(다른 곳에 설치된 같은 이름의 패키지가 아닌지). 그렇지 않으면 통과한 척하지 않고 즉시 알려 줍니다.
3. 모든 테스트를 한 번 실행하고 각 테스트가 어떤 파일을 읽었는지 기록합니다. 이 실행은 시간을 절약하지 않으며, 기록을 만드는 단계입니다.
4. 앞으로 매번 대략 얼마나 절약할지, 그리고 어떤 파일이 바뀌면 가장 많은 테스트에 영향을 주는지 알려 줍니다.

## 2. 푸시하기 전마다 다음과 같은 미리 보기가 표시됩니다

아래는 aib 프로젝트 자체의 실제 푸시입니다(**예시이며, 여러분 프로젝트의 숫자는 다릅니다**):

```
This pre-push check: 929 tests
├─ without aib (one test after another): about 3.2 h
├─ with aib (this machine): about 10 min — started 07:01, expected to finish 07:11
└─ more machines together (planned, projected): 2 machines about 8.3 min, 4 machines about 8.3 min
```

읽는 방법:
- **Without aib**: 모든 테스트를 하나씩 차례로 실행했을 때 걸리는 시간(각 테스트의 마지막 실제 실행 시간의 합).
- **With aib**: 이번에는 영향받는 테스트만 실행하며, 이를 샤드로 나눠 나란히 실행합니다. 예상 소요 시간과 종료 시각입니다.
- **More machines**: 유휴 머신이 작업을 나눠 맡으면 얼마나 빨라지는지에 대한 예상치입니다(가장 느린 단일 테스트가 하한이므로 머신이 일정 수를 넘으면 더 빨라지지 않습니다).

## 3. 실행 후 정산

```
This pre-push check, in the end
├─ without aib you would wait: about 3.2 h
├─ with aib it actually took: 11.4 min (3 h less waiting, about 94%)
│  └─ extra savings from shadow resume: about 52.7 min (44 tests reused)
└─ since the first run: 6 pushes; without aib 19.6 h, actual 4.9 h, 14.7 h less waiting in total
```

- "Less waiting"은 두 부분으로 이루어집니다: **나란히 실행**(여러 샤드를 동시에)으로 절약한 시간과, 그 위에 **섀도 재개**(영향받지 않은 테스트가 지난 결과를 재사용)로 추가 절약한 시간입니다.
- "Since the first run"은 aib를 처음 사용한 이후의 누적 합계입니다. 모든 실행은 추정이 아니라 실제 일어난 그대로 더해집니다.
- 실행별·주별 상세 내용: `aib ship stats`.

## 4. 가장 많이 절약되는 경우(그리고 할 수 있는 일)

| 바꾼 것 | 일어나는 일 | 할 수 있는 일 |
|---|---|---|
| 일반 코드 | 그 코드를 읽는 테스트만 다시 실행되며, 절약이 가장 큼 | 평소대로 푸시 |
| 많은 테스트가 읽는 파일(공유 설정, 공유 유틸리티) | 그 파일을 읽는 모든 테스트가 다시 실행됨 | **이 파일들의 변경을 한 번의 푸시로 모아서**, 다른 푸시에서 재사용이 많이 일어나게 합니다. `aib ship hotfiles`가 해당 파일을 알려 줍니다 |
| 번역 사전(문자열마다 키 하나) | **그 문자열을 사용하는** 테스트만 다시 실행됨 | 평소대로 푸시. 사전 전체를 비교하거나 복사하는 테스트는 여전히 다시 실행됩니다(올바른 동작) |
| 패키지 잠금 파일(requirements, lock 파일) | 전부 다시 실행됨 | 패키지는 여러 번에 나누지 말고 한꺼번에 업그레이드 |

`aib ship stats`의 마지막 줄들은 **여러분 자신의 푸시 이력**을 바탕으로 알려 줍니다: 일반 코드만 바꾼 푸시에서 평균 몇 개의 테스트가 재사용되었고 그것이 전체 테스트 시간의 몇 퍼센트였는지,
그리고 푸시 중 몇 번이 "전부 다시 실행" 경우에 해당했는지.

## 5. 꼭 알아야 할 세 가지

1. **빨간 결과가 녹색으로 재사용되는 일은 없습니다.** 결과는 "지난번에 녹색이었고 읽은 파일이 하나도 바뀌지 않은" 경우에만 재사용됩니다. 측정할 수 없는 것을 읽는 테스트는 매번 실제로 실행됩니다.
2. **"미검증"은 녹색이 아닙니다.** 이 머신에 필요한 도구가 없는 테스트(예: Mac 전용 명령)나 측정할 수 없는 테스트는 정직하게 "미검증"으로 표시되며 통과로 집계되지 않습니다.
3. **재사용이 올바른지 확인하려면**: `aib ship audit`가 "재사용된" 테스트 일부를 실제로 다시 실행해 결과를 비교하고 합계에 기록합니다.

## 6. 아직 지원하지 않는 것

- Windows.
- Python 이외의 언어: 지원 범위는 `AIB-TEST-QUICKSTART.ko.md`의 언어 표에 있습니다(Node, jest/mocha, Go, PHP로 테스트함. vitest는 올바르게 실행되지만 매번 다시 실행됨. Ruby, Rust, Java 등은 명령을 직접 선언해야 하며 매번 실제로 실행됨).
- 시계만 보고 파일은 읽지 않는 시간 의존 테스트(예: "30일이 지나면 만료로 간주")는 기록 만료로만 대응하므로, 최대 14일 뒤에야 다시 실행될 수 있습니다.

