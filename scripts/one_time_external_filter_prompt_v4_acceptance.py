"""One-time, read-only acceptance test for external-filter positive prompts v4.

The 100-article cohort is recovered from the v3 acceptance CSV. PostgreSQL is
opened read-only. Production prompt construction, payload construction, HTTP
invocation, response capture, score parsing, and CSV fields are reused without
changing production code or configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import one_time_external_filter_determinism as baseline
from scripts import one_time_external_filter_prompt_v2_acceptance as v2
from scripts import one_time_external_filter_prompt_v3_acceptance as v3
from src.adapters.external_filter_model import build_prompt, load_prompt_versions
from src.config import get_settings
from src.domain import ExternalFilterCandidate

SAMPLE_SOURCE = Path("artifacts/external_filter_prompt_v3_acceptance_calls.csv")
DEFAULT_REPORT_PATH = Path("artifacts/external_filter_prompt_v4_acceptance_report.md")
DEFAULT_CSV_PATH = Path("artifacts/external_filter_prompt_v4_acceptance_calls.csv")
SAMPLE_SEED = "external-filter-prompt-v3-acceptance-20260809"
INVOCATION_SEED = "external-filter-prompt-v4-acceptance-20260809"
FIXED_PROVIDER = "alibaba/fp8"
SAMPLE_REPETITIONS = 3
REGRESSION_REPETITIONS = 5
CONCURRENCY = 8


@dataclass(frozen=True, slots=True)
class RegressionCase:
    article_id: str
    label: str
    category: str
    minimum: int
    maximum: int
    expected_rule: str
    failure_analysis: str
    priority_fix: bool = False
    external_page: bool = False

    @property
    def expectation(self) -> str:
        if self.minimum == 0:
            return f"稳定 {self.minimum}-{self.maximum}"
        if self.maximum == 100:
            return f"稳定 ≥{self.minimum}"
        return f"稳定 {self.minimum}-{self.maximum}"


REGRESSION_CASES = (
    RegressionCase(
        v2.TARGET_ARTICLE_ID,
        "市政府常务会议研究国际科技创新中心建设",
        "internal_positive",
        85,
        100,
        "internal Tier 1-G（市级决策部署中的具体教育内容）",
        "Tier 1-G 明确覆盖综合性市级会议中零散但具体的教育工作要求。",
        external_page=True,
    ),
    RegressionCase(
        "7575141576054211087",
        "河南两部门发文规范学校餐饮具清洗消毒",
        "external_positive",
        50,
        100,
        "external Tier 1-B（省级学校专项治理）",
        "Tier 1-B 明确把以学校师生为对象的安全监管、食品卫生纳入重大教育治理。",
    ),
    RegressionCase(
        "7659340936928887336",
        "大理巍山流动科普试点",
        "external_positive",
        0,
        19,
        "external Tier 3-E（京外科普活动）",
        "Tier 3-E 明确排除京外科普活动，即使活动含培训或教师字样。",
    ),
    RegressionCase(
        "chinanews:/gn/2026/05-10/10618741",
        "重庆科普讲解大赛",
        "external_positive",
        0,
        19,
        "external Tier 3-E（京外科普赛事）",
        "Tier 3-E 明确排除京外科普赛事。",
    ),
    RegressionCase(
        "jyb:/rmtxwwyyq/jyxx1306/202607/t20260706_2111499339",
        "四川老年健康教育专委会学术会议",
        "external_positive",
        0,
        19,
        "external Tier 3-E（京外老年健康教育）",
        "Tier 3-E 明确排除京外老年健康教育与老年大学。",
    ),
    RegressionCase(
        "chinanews:/gn/2026/05-25/10627892",
        "首届中国—巴尔干文化交流论坛（在京举行）",
        "internal_positive",
        35,
        49,
        "internal Tier 4-C（在京文化交流论坛）",
        "Tier 2-E 明确排除文化交流议题，Tier 4-C 是专门落点；但高校主办例外仍可能触发 Tier 3-F。",
        priority_fix=True,
    ),
    RegressionCase(
        "7631724657380557363",
        "300余家国企招聘岗位上线",
        "internal_positive",
        0,
        19,
        "internal Tier 6-D（招聘公告硬性排除）",
        "Tier 6-D 是体裁型硬排除，命中后不再与教育部或就业平台主体竞争。",
        priority_fix=True,
    ),
    RegressionCase(
        "tencent:20260618A04KRO00",
        "2027QS世界大学排名发布",
        "internal_positive",
        50,
        69,
        "internal Tier 3-H（含北京高校的第三方排名）",
        "Tier 3-H 是第三方高校排名的专门条款，且文中含北京高校时 Tier 6-B 不成立。",
        priority_fix=True,
    ),
    RegressionCase(
        "chinanews:/gn/shipin/2026/03-07/news1048284",
        "政府工作报告首提“中小学春秋假”",
        "external_positive",
        35,
        100,
        "external Tier 1-B / Tier 2-C（政府教育政策或权威教育信息）",
        "明确教育政策标题不应被 Tier 3-C/F 随机归零；正文信息较短时至少应稳定越过 35。",
        priority_fix=True,
    ),
    RegressionCase(
        "jyb:/rmtzgjyb/202606/t20260611_2111490047",
        "多地增加高中学位供给",
        "internal_positive",
        35,
        100,
        "internal Tier 3-G / Tier 5-C（教育观察或教育兜底）",
        "正文明确讨论高中学位供给且含北京案例，Tier 6-B/C 的客观事实条件均不成立。",
        priority_fix=True,
    ),
    RegressionCase(
        "7575087565024379392",
        "深耕古诗文教学 赋能情感育人",
        "external_positive",
        35,
        100,
        "external Tier 2-A（局部课程与教学实践）",
        "正文明确以学校教学为对象，Tier 3-F 不成立；Tier 2-A 是局部教学实践的直接落点。",
        priority_fix=True,
    ),
)


STATIC_ASSESSMENTS: dict[tuple[str, str], tuple[str, str]] = {
    ("internal_positive", "体育"): (
        "有",
        "Tier 1-D 与 Tier 3-D 以市级主办、跨区覆盖和系统机制区分；Tier 4-C 明确将青少年体育送回这两项。",
    ),
    ("internal_positive", "体教融合"): (
        "有",
        "Tier 1-D/Tier 3-D 按市级覆盖与具体实践分层，Tier 4-C 明确不适用于青少年体教融合。",
    ),
    ("internal_positive", "论坛"): (
        "有",
        "Tier 2-E 要求全国性且议题为教育、科技或人才；Tier 3-F 是单校论坛；文化交流论坛由 Tier 2-E 排除并落 Tier 4-C。",
    ),
    ("internal_positive", "文化"): (
        "部分",
        "Tier 2-E 与文化论坛主题互斥，Tier 2-C/3-C 主要按区级主体与单校主体区分，Tier 4-C 有学校主体例外；但 Tier 3-C 的“单个单位”与 Tier 4-C 的“社会机构”存在重叠。",
    ),
    ("internal_positive", "科研"): (
        "有",
        "Tier 1-G 是市级决策要求，Tier 3-B 要求北京高校为成果主体；Tier 4-C/5-A 中只是核心业务边界说明，不是独立提档条件。",
    ),
    ("internal_positive", "科技"): (
        "部分",
        "Tier 1-E/G、Tier 2-E、Tier 3-B/F 均有主体或活动类型约束，但全国科技论坛与单校科研论坛仍依赖模型正确识别主办层级。",
    ),
    ("internal_positive", "人才"): (
        "部分",
        "各条分别要求领导教育活动、市级品牌、教育支援、专业布局或论坛主体；Tier 1-G 明确仅有宽泛人才表述不适用，但跨条款仍依赖主体识别。",
    ),
    ("internal_positive", "思政"): (
        "有",
        "Tier 2-C 与 Tier 3-C 以区级主体/覆盖和单校主体区分；Tier 4-C 中只作为市教委核心业务边界举例。",
    ),
    ("internal_positive", "青少年"): (
        "有",
        "Tier 1-D/Tier 3-D 按市级覆盖与具体机制区分，Tier 2-C 另要求区级思政艺术活动，Tier 4-C 明确排除体教融合。",
    ),
    ("internal_positive", "健康"): (
        "有",
        "学生身心健康按 Tier 1-D/3-D 的主体规模裁定，老年健康教育落 Tier 4-C，普通市民健康落 Tier 5-A。",
    ),
    ("internal_positive", "治理"): (
        "有",
        "市教委权威治理发布由主体优先规则固定 Tier 2-B，评论观察和低价值教育议题分别落 Tier 3-G/5-C。",
    ),
}


UNSTABLE_DIAGNOSES: dict[tuple[str, str], str] = {
    ("A", "7575087565024379392"): "external Tier 2-A（局部课程教学实践） ↔ Tier 3-C（把单校教研视为低借鉴教育资讯）",
    ("A", "7576077820816884262"): "internal Tier 3-B（误把院士增选及北大教授当成北京高校科研成果） ↔ Tier 5-C（科技人才报道仅顺带涉及高校）",
    ("A", "7584376512065929737"): "external Tier 2-A/2-C（局部教学管理实践或一般权威信息） ↔ Tier 3-A/3-C（单校常规检查、借鉴价值低）",
    ("A", "7605519670547857954"): "internal Tier 5-C 同一条款内部跨阈值（北林大花卉成果未达到 Tier 3-B 的重大科研标准）",
    ("A", "7628162448122331686"): "internal Tier 4-A（单校校园体育活动） ↔ Tier 5-C（无政策或推广价值的普通校内赛事）",
    ("A", "7632134103197811254"): "internal Tier 4-C（在京文化交流边界活动） ↔ Tier 5-C（京外艺术院团的普通展演交流）",
    ("A", "7634356249621987867"): "internal Tier 3-G（专业学位培养改革观察） ↔ Tier 5-C（京外高校个案、报送价值有限）",
    ("A", "7636001703149486619"): "external Tier 2-A/2-B（把工业研学游当作教育实践或交流活动） ↔ Tier 3-E/3-C（京外科普文旅、教育并非主体）",
    ("A", "7648848244943962659"): "internal Tier 5-C 同一条款内部跨阈值（全国高考外媒综述虽含北京考点，但无明确北京教育工作价值）",
    ("A", "7665280166154781238"): "internal Tier 1-C/1-D（误把国家体育规划当成教育顶层设计或北京市级体教融合） ↔ Tier 6-C（体育政策主体并非教育）",
    ("A", "7671187122530812457"): "internal Tier 5-C 同一条款内部跨阈值（学校门口城市微改造）并与 Tier 4-A（小范围校园相关活动）相邻",
    ("A", "chinadaily:/a/202512/03/WS69301868a310942cc4994cc5"): "internal Tier 5-C 同一条款内部跨阈值（科研趋势论坛无北京高校成果主体）",
    ("A", "jyb:/rmtsy1240/zt/jyyzpzt/202603/t20260310_2111452618"): "internal Tier 3-G（AI 时代教育评论与观察） ↔ Tier 5-C（正文近乎只有嘉宾名单、实质内容不足）",
    ("A", "jyb:/rmtzgjyb/202606/t20260611_2111490047"): "internal Tier 3-G（全国高中学位供给的教育观察） ↔ Tier 6-B/5-C（京外为主或仅属低价值教育议题）",
    ("A", "jyb:/talents/202604/t20260421_2111469764"): "internal Tier 5-C 同一条款内部跨阈值（区域人才就业创业政策仅顺带出现高校）",
    ("A", "jyb:/talents/202605/t20260520_2111480859"): "internal Tier 4-C（误把人才规划中的培训/教育资源视作边界教育题材） ↔ Tier 5-C（人才政策仅顺带涉及教育）",
    ("A", "tencent:20260604A09MJP00"): "external Tier 1-C/1-D（误把技能人才制度或趋势当成教育治理创新/教育报告） ↔ Tier 3-E（京外职业技能竞赛）",
    ("A", "tencent:20260607A02IBY00"): "external Tier 2-C（一般权威教育信息） ↔ Tier 3-C/3-D（高考当天事实或实用资讯）",
    ("D", "7571345639725662763"): "external Tier 2-A（有方法细节的劳动教育课堂） ↔ Tier 3-A/3-C（单校课堂展示、借鉴价值低）",
    ("D", "7578798004317078056"): "external Tier 1-A/1-D（全国教育深度总结或系统趋势） ↔ Tier 3-C（被当成空泛宣传）",
    ("D", "7584732182552379919"): "internal Tier 2-F/3-B（误把中科院成果榜单当北京教育系统报道或高校科研成果） ↔ Tier 6-C（无北京高校教育主体）",
    ("D", "7614650800782017060"): "external Tier 1-B/1-C（误把区域青年人才政策当重大教育政策或教育治理创新） ↔ Tier 3-C（就业人才领域、教育非主体）",
    ("D", "7636001703149486619"): "external Tier 2-A/2-B（工业研学游被当作教育实践或活动） ↔ Tier 3-E/3-C（科普文旅、教育非主体）",
    ("D", "7648848244943962659"): "internal Tier 3-G（全国高考教育观察） ↔ Tier 6-B/6-C（外媒综述、北京教育并非主体）",
    ("D", "7658936010105815567"): "internal Tier 2-E（全国性青少年科技人才赛事） ↔ Tier 6-C（社会机构数字体育赛事、无学校教育主体）",
    ("D", "7670053228096061971"): "internal Tier 4-C（在京科普与青少年安全边界活动） ↔ Tier 6-C（科普活动不被视为具体教育对象）",
    ("D", "7671187122530812457"): "internal Tier 2-B（误作教育治理或校园安全权威措施） ↔ Tier 6-C（城市便民设施、学校只是地点）",
    ("D", "chinadaily:/a/202512/03/WS69301868a310942cc4994cc5"): "internal Tier 3-B/3-F（误作高校科研成果或教育论坛） ↔ Tier 6-C（中科院科技趋势报告、教育非主体）",
    ("D", "chinanews:/sh/2026/01-26/10559256"): "external Tier 1-C/2-A（全省托管机制或局部教育实践） ↔ Tier 3-A/3-C（一般青少年活动、教育借鉴不足）",
    ("D", "chinanews:/sh/2026/04-09/10600861"): "external Tier 1-C/2-A（县域传统文化进校园机制或局部育人实践） ↔ Tier 3-A（单校戏曲社团活动）",
    ("D", "jyb:/rmtzgjyb/202511/t20251124_2111416917"): "external Tier 1-C（区域全员关爱导师机制） ↔ Tier 3-D（教师个人事迹硬性排除）",
    ("D", "jyb:/rmtzgjyb/202606/t20260611_2111490047"): "internal Tier 3-G（全国高中学位供给教育观察） ↔ Tier 5-C（京外为主、北京案例有限）",
    ("D", "qianlong:2026/0424/8659072"): "internal Tier 2-A（误把京津冀科创产业协同当教育资源协同） ↔ Tier 6-C（产业科技报道无教育对象）",
    ("D", "tencent:20260604A09MJP00"): "external Tier 1-C/1-D（技能人才机制被误作教育治理或教育趋势） ↔ Tier 3-E（京外职业技能竞赛）",
}


def _artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    root = (_REPO_ROOT / "artifacts").resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Output must stay under artifacts/: {path}")
    return resolved


def _load_v3_cohort(path: Path) -> tuple[list[str], list[baseline.CallResult]]:
    if not path.exists():
        raise FileNotFoundError(f"v3 calls CSV is missing: {path}")
    calls = baseline._read_csv(path)
    sample_ids = sorted({row.article_id for row in calls if row.config_name == "A"})
    if len(sample_ids) != 100:
        raise RuntimeError(f"Expected 100 v3 sample articles, found {len(sample_ids)}")
    for config_name in ("A", "D"):
        slots = Counter(
            row.article_id
            for row in calls
            if row.article_id in sample_ids and row.config_name == config_name
        )
        if set(slots) != set(sample_ids) or set(slots.values()) != {3}:
            raise RuntimeError(f"v3 {config_name} sample slots are incomplete")
    return sample_ids, calls


def _fetch_sample(article_ids: Sequence[str]) -> list[dict[str, Any]]:
    rows = v2._fetch_same_rows(article_ids)
    by_id = {str(row.get("article_id") or ""): row for row in rows}
    missing = set(article_ids) - set(by_id)
    if missing:
        raise RuntimeError(f"v3 cohort rows missing from database: {sorted(missing)}")
    return [by_id[article_id] for article_id in article_ids]


def _fetch_regressions() -> tuple[dict[str, tuple[ExternalFilterCandidate, float]], int, bool, str]:
    database_ids = [case.article_id for case in REGRESSION_CASES if not case.external_page]
    rows = v2._fetch_same_rows(database_ids)
    by_id: dict[str, tuple[ExternalFilterCandidate, float]] = {}
    for row in rows:
        candidate = baseline._candidate_from_row(row)
        case = next(case for case in REGRESSION_CASES if case.article_id == candidate.article_id)
        category = str(row.get("category") or "")
        if category != case.category:
            raise RuntimeError(f"Regression category drift for {candidate.article_id}: {category}")
        by_id[candidate.article_id] = (
            candidate,
            float(row.get("external_importance_score") or 0),
        )
    expected = {case.article_id for case in REGRESSION_CASES if not case.external_page}
    if set(by_id) != expected:
        raise RuntimeError(f"Regression rows incomplete: missing={sorted(expected - set(by_id))}")
    target, chars, truncated, method = v2._fetch_target_article()
    by_id[target.article_id] = (target, 0.0)
    return by_id, chars, truncated, method


def _build_plan(
    candidate: ExternalFilterCandidate,
    *,
    category: str,
    stored_score: float,
    config_name: str,
    repetition: int,
) -> baseline.InvocationPlan:
    prompt = build_prompt(candidate, category=category)
    payload = baseline._payload_for_config(prompt, config_name, FIXED_PROVIDER)
    return baseline.InvocationPlan(
        article_id=candidate.article_id,
        title=candidate.title or "",
        stored_score=stored_score,
        score_band=min(int(stored_score // 20), 4),
        category=category,
        config_name=config_name,
        repetition=repetition,
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        common_payload_sha256=baseline._stable_hash(baseline._common_payload(payload)),
        request_payload_sha256=baseline._stable_hash(payload),
        payload=payload,
    )


def _build_plans(
    sample_rows: Sequence[Mapping[str, Any]],
    regressions: Mapping[str, tuple[ExternalFilterCandidate, float]],
) -> list[baseline.InvocationPlan]:
    plans: list[baseline.InvocationPlan] = []
    sample_ids = {str(row.get("article_id") or "") for row in sample_rows}
    for row in sample_rows:
        candidate = baseline._candidate_from_row(row)
        category = str(row.get("category") or "")
        if category not in {"internal_positive", "external_positive"}:
            raise RuntimeError(f"Unsupported sample category: {category}")
        for config_name in ("A", "D"):
            for repetition in range(1, SAMPLE_REPETITIONS + 1):
                plans.append(
                    _build_plan(
                        candidate,
                        category=category,
                        stored_score=float(row.get("external_importance_score") or 0),
                        config_name=config_name,
                        repetition=repetition,
                    )
                )
    for case in REGRESSION_CASES:
        candidate, stored_score = regressions[case.article_id]
        first_repetition = 4 if case.article_id in sample_ids else 1
        for repetition in range(first_repetition, first_repetition + REGRESSION_REPETITIONS):
            plans.append(
                _build_plan(
                    candidate,
                    category=case.category,
                    stored_score=stored_score,
                    config_name="D",
                    repetition=repetition,
                )
            )
    random.Random(f"{INVOCATION_SEED}:invocations").shuffle(plans)
    baseline._assert_only_intended_variables(plans)
    keys = {(plan.article_id, plan.config_name, plan.repetition) for plan in plans}
    if len(plans) != 655 or len(keys) != len(plans):
        raise RuntimeError(f"Expected 655 unique invocation slots, got {len(plans)}/{len(keys)}")
    return plans


def _validate_payloads(plans: Sequence[baseline.InvocationPlan]) -> dict[str, Any]:
    result = v3._validate_payloads(plans)
    by_article: dict[str, dict[str, baseline.InvocationPlan]] = defaultdict(dict)
    for plan in plans:
        if plan.repetition <= 3:
            by_article[plan.article_id][plan.config_name] = plan
    for article_id, pair in by_article.items():
        if set(pair) != {"A", "D"}:
            continue
        if pair["A"].prompt_sha256 != pair["D"].prompt_sha256:
            raise RuntimeError(f"A/D prompt differs for {article_id}")
        if pair["A"].common_payload_sha256 != pair["D"].common_payload_sha256:
            raise RuntimeError(f"A/D common payload differs for {article_id}")
    return result


def _group_sample_scores(
    calls: Sequence[baseline.CallResult],
    sample_ids: set[str],
    config_name: str,
) -> dict[str, list[int]]:
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in calls:
        if (
            row.article_id in sample_ids
            and row.config_name == config_name
            and 1 <= row.repetition <= SAMPLE_REPETITIONS
            and row.status == "ok"
            and row.parsed_score is not None
        ):
            grouped[row.article_id].append((row.repetition, int(row.parsed_score)))
    result = {article_id: [score for _, score in sorted(values)] for article_id, values in grouped.items()}
    if set(result) != sample_ids or any(len(scores) != 3 for scores in result.values()):
        raise RuntimeError(f"Incomplete {config_name} sample scores")
    return result


def _regression_scores(
    calls: Sequence[baseline.CallResult],
    sample_ids: set[str],
) -> dict[str, list[int]]:
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    regression_ids = {case.article_id for case in REGRESSION_CASES}
    for row in calls:
        first = 4 if row.article_id in sample_ids else 1
        if (
            row.article_id in regression_ids
            and row.config_name == "D"
            and first <= row.repetition < first + 5
            and row.status == "ok"
            and row.parsed_score is not None
        ):
            grouped[row.article_id].append((row.repetition, int(row.parsed_score)))
    result = {article_id: [score for _, score in sorted(values)] for article_id, values in grouped.items()}
    if set(result) != regression_ids or any(len(scores) != 5 for scores in result.values()):
        raise RuntimeError("Incomplete regression scores")
    return result


def _v3_metrics(calls: Sequence[baseline.CallResult], sample_ids: set[str], categories: Mapping[str, str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for config_name in ("A", "D"):
        grouped = _group_sample_scores(calls, sample_ids, config_name)
        result[config_name] = sum(
            v3._is_decision_stable(categories[article_id], scores)
            for article_id, scores in grouped.items()
        )
    if result != {"A": 84, "D": 90}:
        raise RuntimeError(f"v3 baseline drift: {result}")
    return result


def _verdict(a_stable: int) -> tuple[str, str]:
    if a_stable >= 88:
        return "通过", "达到目标值（≥88/100），建议上线"
    if a_stable >= 84:
        return "通过", "按规定判定通过并建议上线，但未达到 88/100 目标值"
    return "不通过", "低于 84/100，v4 结构性改动未奏效"


def _diagnosis(category: str, scores: Sequence[int]) -> str:
    low_tier = v3._score_tier(category, min(scores))
    high_tier = v3._score_tier(category, max(scores))
    if low_tier == high_tier:
        return f"{low_tier} 内部跨验收阈值；需结合该档具体条目复核"
    return f"{low_tier} ↔ {high_tier}；需结合正文复核具体条目"


def _render_report(
    *,
    sample_rows: Sequence[Mapping[str, Any]],
    v3_calls: Sequence[baseline.CallResult],
    calls: Sequence[baseline.CallResult],
    static_scan: Mapping[str, Mapping[str, Sequence[str]]],
    versions: Mapping[str, str],
    payload_check: Mapping[str, Any],
    target_chars: int,
    target_truncated: bool,
    target_fetch_method: str,
    timeout: int,
    retries: int,
    csv_path: Path,
) -> str:
    sample_ids = {str(row.get("article_id") or "") for row in sample_rows}
    metadata = {
        str(row.get("article_id") or ""): {
            "title": str(row.get("title") or ""),
            "category": str(row.get("category") or ""),
        }
        for row in sample_rows
    }
    categories = {article_id: str(data["category"]) for article_id, data in metadata.items()}
    v3_metrics = _v3_metrics(v3_calls, sample_ids, categories)
    grouped = {
        config_name: _group_sample_scores(calls, sample_ids, config_name)
        for config_name in ("A", "D")
    }
    unstable: dict[str, list[str]] = {}
    stable: dict[str, int] = {}
    for config_name in ("A", "D"):
        unstable[config_name] = sorted(
            article_id
            for article_id, scores in grouped[config_name].items()
            if not v3._is_decision_stable(categories[article_id], scores)
        )
        stable[config_name] = 100 - len(unstable[config_name])
    verdict, verdict_note = _verdict(stable["A"])

    regression_scores = _regression_scores(calls, sample_ids)
    regression_passed = {
        case.article_id: all(case.minimum <= score <= case.maximum for score in regression_scores[case.article_id])
        for case in REGRESSION_CASES
    }
    statuses = Counter(row.status for row in calls)
    providers = {
        config_name: Counter(
            row.response_provider or "(missing)"
            for row in calls
            if row.article_id in sample_ids and row.config_name == config_name and row.repetition <= 3
        )
        for config_name in ("A", "D")
    }
    models = Counter(row.response_model or "(missing)" for row in calls)
    strata = Counter(
        (
            "internal" if str(row.get("category") or "") == "internal_positive" else "external",
            min(int(float(row.get("external_importance_score") or 0) // 20), 4),
        )
        for row in sample_rows
    )
    candidates = [
        (prompt_name, keyword, list(locations))
        for prompt_name, keyword_map in static_scan.items()
        for keyword, locations in keyword_map.items()
        if v3._tier_count(locations) >= 3
    ]

    lines = [
        "# 四分类正面提示词 v4 验收报告",
        "",
        "## 技术结论",
        "",
        f"**主验收结论：{verdict}。** v4 的 A 组决策稳定性为 {stable['A']}/100；{verdict_note}。D 组为 {stable['D']}/100，仅作低噪诊断。11 条定点回归通过 {sum(regression_passed.values())}/11，作为逐题材风险记录，不擅自改变题目规定的 A 组主判定规则。",
        "",
        f"- 调用完整性：计划 655 次，CSV {len(calls)} 行；状态分布 {dict(statuses)}；响应 model {dict(models)}",
        f"- 提示词版本：internal_positive=`{versions['internal_positive']}`、external_positive=`{versions['external_positive']}`；internal_negative=`{versions['internal_negative']}`、external_negative=`{versions['external_negative']}`（负面提示词未调用）",
        f"- 样本来源：直接读取 `{SAMPLE_SOURCE.as_posix()}` 中 A 组的 100 个唯一 article_id；internal/external 各 50 条，原分数五个 20 分档各 10 条/类别（实际分层 {dict(sorted(strata.items()))}）；记录原抽样种子 `{SAMPLE_SEED}`，没有重新抽样",
        "- 本报告只以过/不过决策稳定性验收；不使用“分数完全一致”或“整档跳条数”作为通过条件",
        "",
        "## 第一部分：决策稳定性",
        "",
        "同一文章同一配置的 3 次分数若在阈值下给出同一个过/不过结论，即为决策稳定。internal_positive 阈值为 30、external_positive 为 35；两者只用于本报告计算，未修改生产配置。",
        "",
        "| 版本 | A 组决策稳定 | D 组决策稳定 |",
        "|---|---:|---:|",
        f"| v3（从上轮 CSV 复算） | {v3_metrics['A']}/100 | {v3_metrics['D']}/100 |",
        f"| v4 | **{stable['A']}/100** | {stable['D']}/100 |",
        "",
        f"按指定三档规则：{verdict_note}。",
        "",
        "### 决策不稳定文章",
        "",
    ]
    for config_name in ("A", "D"):
        lines.extend([f"#### {config_name} 组：{len(unstable[config_name])} 条", ""])
        if not unstable[config_name]:
            lines.extend(["无。", ""])
            continue
        lines.extend([
            "| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |",
            "|---|---|---|---|---|---|",
        ])
        for article_id in unstable[config_name]:
            scores = grouped[config_name][article_id]
            category = categories[article_id]
            decisions = ["过" if score >= v3._threshold(category) else "不过" for score in scores]
            diagnosis = UNSTABLE_DIAGNOSES.get(
                (config_name, article_id),
                _diagnosis(category, scores),
            )
            lines.append(
                f"| {article_id} | {v3._escape(str(metadata[article_id]['title']))} | {category} | {'/'.join(map(str, scores))} | {'/'.join(decisions)} | {v3._escape(diagnosis)} |"
            )
        lines.append("")

    lines.extend([
        "### 辅助分布（不作为通过条件）",
        "",
        "| 配置 | 类别 | 平均分 | 逐篇极差中位数 | 各档位分数取值个数（0-19 / 20-39 / 40-59 / 60-79 / 80-100） |",
        "|---|---|---:|---:|---|",
    ])
    for config_name in ("A", "D"):
        for category in ("internal_positive", "external_positive"):
            ids = [article_id for article_id in sample_ids if categories[article_id] == category]
            values = [score for article_id in ids for score in grouped[config_name][article_id]]
            ranges = [max(grouped[config_name][article_id]) - min(grouped[config_name][article_id]) for article_id in ids]
            distinct = [
                len({score for score in values if low <= score <= high})
                for low, high in ((0, 19), (20, 39), (40, 59), (60, 79), (80, 100))
            ]
            lines.append(
                f"| {config_name} | {category} | {mean(values):.2f} | {median(ranges):.1f} | {' / '.join(map(str, distinct))} |"
            )
    lines.extend([
        "",
        "| 分数段 | A 组 | D 组 |",
        "|---|---:|---:|",
    ])
    histograms = {
        name: v3._histogram([score for scores in grouped[name].values() for score in scores])
        for name in ("A", "D")
    }
    for index, (label, _) in enumerate(histograms["A"]):
        lines.append(f"| {label} | {histograms['A'][index][1]} | {histograms['D'][index][1]} |")

    lines.extend([
        "",
        "## 第二部分：11 条定点回归",
        "",
        f"**结果：{sum(regression_passed.values())}/11 通过。** 三条与主样本重叠的文章仍额外执行了 5 次全新 D 调用，在 CSV 中用 repetition=4-8 标识；没有复用主实验的 3 次结果。",
        "",
        "| 文章 | 类别 | 期望 | D 组 5 次 | 结果 | 对应规则 |",
        "|---|---|---|---|---|---|",
    ])
    for case in REGRESSION_CASES:
        scores = regression_scores[case.article_id]
        lines.append(
            f"| {v3._escape(case.label)} | {case.category} | {case.expectation} | {'/'.join(map(str, scores))} | {'通过' if regression_passed[case.article_id] else '**不通过**'} | {v3._escape(case.expected_rule)} |"
        )
    lines.extend([
        "",
        f"市政府常务会议原文未从库中取用；脚本沿用前轮方式对[北京日报客户端原文]({v2.TARGET_URL})执行 HTTP GET，从 `{target_fetch_method}` 抽取正文。正文字数 {target_chars}，1500 字截断：{'是' if target_truncated else '否'}。",
    ])
    failed_priority = [case for case in REGRESSION_CASES if case.priority_fix and not regression_passed[case.article_id]]
    if failed_priority:
        lines.extend(["", "### v4 重点修复对象未通过项：原始输出与规则判断", ""])
        for case in failed_priority:
            first = 4 if case.article_id in sample_ids else 1
            rows = sorted(
                [row for row in calls if row.article_id == case.article_id and row.config_name == "D" and first <= row.repetition < first + 5],
                key=lambda row: row.repetition,
            )
            lines.extend([
                f"#### {v3._escape(case.label)}：{'/'.join(map(str, regression_scores[case.article_id]))}",
                "",
                f"判断：未稳定落入 `{case.expectation}`。{case.failure_analysis}",
                "",
            ])
            for index, row in enumerate(rows, start=1):
                lines.extend([f"第 {index} 次（CSV repetition={row.repetition}，parsed={row.parsed_score}）：", "", "```text", row.raw_output, "```", ""])

    lines.extend([
        "## 第三部分：关键词跨档位静态检查",
        "",
        "扫描对象为当前两份 v4 正面提示词。位置按 Tier 条目去重；跨越档位数统计不同 Tier 数，不把裁定规则或小节标题计入 Tier。",
        "",
        "| 提示词 | 关键词 | 出现位置列表 | 跨越档位数 |",
        "|---|---|---|---:|",
    ])
    for prompt_name in ("internal_positive", "external_positive"):
        for keyword in v3.KEYWORDS:
            locations = list(static_scan[prompt_name][keyword])
            lines.append(
                f"| {prompt_name} | {keyword} | {v3._escape('；'.join(locations) if locations else '—')} | {v3._tier_count(locations)} |"
            )
    lines.extend([
        "",
        "### “文化”四处条目的互斥性判断",
        "",
        "- **Tier 2-C vs Tier 3-C：主体和覆盖面大体可分，但并非逻辑互斥。** 前者要求北京区级党委、政府或相关部门组织且面向全区，后者要求北京单所学校或单个单位组织；区级部门与学校共同主办时可同时命中，但“所有命中取最高档”会裁到 Tier 2-C。",
        "- **Tier 2-E vs Tier 4-C：议题条件明确互斥。** Tier 2-E 明写文化交流、艺术等议题即使规模大也不适用，并指向 Tier 4-C。",
        "- **Tier 3-C vs Tier 4-C：仍有文字重叠。** Tier 4-C 规定由北京高校或中小学实质主办时转到 Tier 3-C/3-F，能处理学校主体；但 Tier 3-C 的“单个单位”可包含社会机构，而 Tier 4-C 也覆盖社会机构，非学校的单一社会机构文化活动可能同时命中。",
        "- **落点缺口：有兜底但不够精确。** 非区级主办、非单校主办、也非论坛或明确社会机构主办的多校文化活动，四个专门条目均可能不完全贴合；可由 Tier 4-A 或最终 Tier 5-C 接住，因此不会无档可归，但落点取决于模型如何理解活动规模。",
        "",
        "结论：四处条件不是严格互斥；现有最高档规则、Tier 4-C 学校主体例外和 Tier 5-C 兜底可完成裁定，但“单个单位/社会机构”是残余候选冲突点。",
        "",
        "### 跨越 3 个及以上档位的候选冲突点",
        "",
    ])
    for prompt_name, keyword, locations in candidates:
        status, assessment = STATIC_ASSESSMENTS.get(
            (prompt_name, keyword),
            ("无", "未发现集中裁定语句；需要依赖每个条目的主体、规模条件和最高档规则。"),
        )
        lines.append(
            f"- **{prompt_name} / {keyword}：裁定状态为{status}。** {assessment}（位置：{'、'.join(locations)}）"
        )

    lines.extend([
        "",
        "## 样本、参数与可复现性",
        "",
        f"- 样本：从 v3 明细 CSV 恢复同一批 100 个 article_id，并只读回取当前库中候选字段；v3 原抽样种子 `{SAMPLE_SEED}`。脚本校验 v3 A/D 各恰有 3 次并复算为 84/100、90/100，否则拒绝运行。",
        f"- A/D 共同参数：生产 `build_prompt`（默认正文 1500 字）、model=`{payload_check['model']}`、temperature=0、timeout={timeout}s、retries={retries}、concurrency={CONCURRENCY}；复用既有 `_invoke`、响应记录与生产 `parse_external_filter_score`。",
        f"- 唯一指定差异：A 不固定 provider，reasoning 维持生产值 `{payload_check['a_reasoning']}`；D 设置 `provider.only=[\"{FIXED_PROVIDER}\"]`、`allow_fallbacks=false`、`reasoning.effort=none`。去除 prompt 后每组 payload skeleton 均只有一个：{payload_check['skeleton_hash_counts']}；脚本逐篇断言 A/D 的 prompt SHA 和 common payload SHA 相同。",
        f"- 实际路由：A={dict(providers['A'])}；D={dict(providers['D'])}。",
        f"- CSV：`{csv_path.as_posix()}`，字段与前三轮 `CSV_FIELDS` 完全一致；655 个计划槽位全部保留 status/error，不静默丢弃。",
        "- 本任务未写数据库、未修改提示词/VERSIONS/adapter/worker/config/阈值，也未注册计划任务。",
        "",
        "## 限制",
        "",
        "- 这是固定分层样本上的重复调用实验，衡量当前模型与当前路由，不保证供应商或模型版本变化后的表现。",
        "- 样本按 v3 的原分数分层，不代表自然生产流量；平均分和直方图只用于版本间辅助观察。",
        "- 对不稳定文章的 Tier 判断是基于标题、正文与 v4 条款的人工归因，不是模型返回的解释（模型按要求只输出整数）。",
        "",
        f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat()}",
    ])
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-time read-only acceptance test for positive importance prompts v4")
    parser.add_argument("--v3-csv", type=Path, default=SAMPLE_SOURCE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if args.concurrency != CONCURRENCY:
        raise ValueError(f"Acceptance requires concurrency={CONCURRENCY}")
    settings = get_settings()
    retries = args.retries or settings.external_filter_max_retries
    timeout = args.timeout or settings.llm_external_filter_timeout
    versions = load_prompt_versions()
    expected_versions = {
        "external_positive": "v4",
        "external_negative": "v1",
        "internal_positive": "v4",
        "internal_negative": "v1",
    }
    if versions != expected_versions:
        raise RuntimeError(f"Unexpected prompt versions: {versions}")

    sample_ids, v3_calls = _load_v3_cohort(args.v3_csv)
    sample_rows = _fetch_sample(sample_ids)
    categories = {str(row.get("article_id") or ""): str(row.get("category") or "") for row in sample_rows}
    prior_categories = {
        row.article_id: row.category
        for row in v3_calls
        if row.article_id in set(sample_ids) and row.config_name == "A"
    }
    if categories != prior_categories:
        drifted = sorted(
            article_id
            for article_id in sample_ids
            if categories.get(article_id) != prior_categories.get(article_id)
        )
        raise RuntimeError(f"v3 cohort category drift: {drifted}")
    _v3_metrics(v3_calls, set(sample_ids), categories)
    regressions, target_chars, target_truncated, target_fetch_method = _fetch_regressions()
    plans = _build_plans(sample_rows, regressions)
    payload_check = _validate_payloads(plans)
    static_scan = v3._static_scan()
    diagnostics = {
        "sample_source": str(args.v3_csv),
        "sample_seed": SAMPLE_SEED,
        "sample_size": len(sample_rows),
        "planned_calls": len(plans),
        "versions": versions,
        "payload_check": payload_check,
        "static_cross_tier_candidates": sum(
            v3._tier_count(locations) >= 3
            for keyword_map in static_scan.values()
            for locations in keyword_map.values()
        ),
        "target_chars": target_chars,
        "target_truncated": target_truncated,
    }
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
    if args.dry_run:
        return 0

    csv_path = _artifact_path(args.csv)
    report_path = _artifact_path(args.report)
    expected_slots = {(plan.article_id, plan.config_name, plan.repetition) for plan in plans}
    if args.render_only:
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV does not exist: {csv_path}")
        calls = baseline._read_csv(csv_path)
        actual_slots = {(row.article_id, row.config_name, row.repetition) for row in calls}
        if actual_slots != expected_slots:
            raise RuntimeError(f"Existing CSV slots differ: expected={len(expected_slots)}, actual={len(actual_slots)}")
    else:
        calls = v2._run_with_resume(
            plans,
            concurrency=args.concurrency,
            retries=retries,
            timeout=timeout,
            csv_path=csv_path,
        )
    if len(calls) != 655 or any(row.status != "ok" or row.parsed_score is None for row in calls):
        failures = Counter(row.status for row in calls if row.status != "ok" or row.parsed_score is None)
        raise RuntimeError(f"Experiment incomplete: rows={len(calls)}, failures={failures}")
    report = _render_report(
        sample_rows=sample_rows,
        v3_calls=v3_calls,
        calls=calls,
        static_scan=static_scan,
        versions=versions,
        payload_check=payload_check,
        target_chars=target_chars,
        target_truncated=target_truncated,
        target_fetch_method=target_fetch_method,
        timeout=timeout,
        retries=retries,
        csv_path=csv_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
