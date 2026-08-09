"""One-time, read-only acceptance test for external-filter positive prompts v3.

The PostgreSQL connection is forced into read-only mode. The script reuses the
production prompt builder and the prior determinism experiment's payload,
invocation, parsing, and CSV schema. It only writes the requested artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
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
from scripts import one_time_external_filter_prompt_v2_acceptance as v2_acceptance
from src.adapters.external_filter_model import build_prompt, load_prompt_versions
from src.config import get_settings
from src.domain import ExternalFilterCandidate

SAMPLE_SEED = "external-filter-prompt-v3-acceptance-20260809"
FIXED_PROVIDER = "alibaba/fp8"
DEFAULT_SAMPLE_SIZE = 100
PILOT_SAMPLE_SIZE = 20
SAMPLE_REPETITIONS = 3
REGRESSION_REPETITIONS = 5
CONCURRENCY = 8
DEFAULT_REPORT_PATH = Path("artifacts/external_filter_prompt_v3_acceptance_report.md")
DEFAULT_CSV_PATH = Path("artifacts/external_filter_prompt_v3_acceptance_calls.csv")
PRIOR_CSV_PATH = Path("artifacts/external_filter_determinism_calls.csv")

PROMPT_PATHS = {
    "internal_positive": Path(
        "config/prompts/internal_positive_importance_prompt.md"
    ),
    "external_positive": Path(
        "config/prompts/external_positive_importance_prompt.md"
    ),
}

KEYWORDS = (
    "体育",
    "体教融合",
    "老年",
    "职业",
    "科普",
    "论坛",
    "文化",
    "科研",
    "科技",
    "评论",
    "排名",
    "榜单",
    "培训",
    "招聘",
    "招考",
    "人才",
    "思政",
    "青少年",
    "社区教育",
    "竞赛",
    "校园安全",
    "招生",
    "教师",
    "个人",
    "广告",
    "艺术",
    "党建",
    "健康",
    "食品卫生",
    "治理",
    "创新",
)

SAMPLE_QUERY = """
    WITH eligible AS (
        SELECT
            article_id,
            title,
            source,
            publish_time_iso,
            url,
            content_markdown,
            llm_summary,
            sentiment_label,
            is_beijing_related,
            is_beijing_related_llm,
            external_importance_status,
            external_filter_fail_count,
            score_details,
            external_importance_score,
            external_importance_checked_at,
            lower(external_importance_raw->>'category') AS category,
            CASE
                WHEN lower(external_importance_raw->>'category') = 'internal_positive'
                    THEN 'internal'
                ELSE 'external'
            END AS base_category,
            LEAST(FLOOR(external_importance_score / 20), 4)::integer AS score_band
        FROM news_summaries
        WHERE external_importance_score IS NOT NULL
          AND external_importance_status IN ('ready_for_export', 'external_filtered')
          AND summary_status = 'completed'
          AND lower(external_importance_raw->>'category') IN (
              'internal_positive',
              'external_positive'
          )
          AND NOT (article_id = ANY(%s))
    ),
    ranked AS (
        SELECT
            eligible.*,
            ROW_NUMBER() OVER (
                PARTITION BY base_category, score_band
                ORDER BY md5(article_id || %s), article_id
            ) AS stratum_rank
        FROM eligible
    )
    SELECT *
    FROM ranked
    WHERE stratum_rank <= %s
    ORDER BY
        CASE base_category WHEN 'internal' THEN 0 ELSE 1 END,
        score_band,
        stratum_rank,
        article_id
"""


@dataclass(frozen=True, slots=True)
class RegressionCase:
    article_id: str
    label: str
    category: str
    minimum: int
    maximum: int
    expected_rule: str
    failure_analysis: str = ""
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
        article_id=v2_acceptance.TARGET_ARTICLE_ID,
        label="市政府常务会议研究国际科技创新中心建设",
        category="internal_positive",
        minimum=85,
        maximum=100,
        expected_rule="internal Tier 1-G（市级决策部署中的具体教育内容）",
        external_page=True,
    ),
    RegressionCase(
        article_id="7575141576054211087",
        label="河南两部门发文规范学校餐饮具清洗消毒",
        category="external_positive",
        minimum=50,
        maximum=100,
        expected_rule="external 前置判定第三项（校园专项治理）及 Tier 1-B / Tier 2-A",
    ),
    RegressionCase(
        article_id="7659340936928887336",
        label="大理巍山流动科普试点",
        category="external_positive",
        minimum=0,
        maximum=19,
        expected_rule="external 前置非教育主体及 Tier 3-E（科普活动）",
    ),
    RegressionCase(
        article_id="chinanews:/gn/2026/05-10/10618741",
        label="重庆科普讲解大赛",
        category="external_positive",
        minimum=0,
        maximum=19,
        expected_rule="external 前置非教育主体及 Tier 3-E（科普赛事）",
    ),
    RegressionCase(
        article_id="jyb:/rmtxwwyyq/jyxx1306/202607/t20260706_2111499339",
        label="四川老年健康教育专委会学术会议",
        category="external_positive",
        minimum=0,
        maximum=19,
        expected_rule="external 前置非教育主体及 Tier 3-E（老年健康教育）",
    ),
    RegressionCase(
        article_id="chinanews:/gn/2026/05-25/10627892",
        label="首届中国—巴尔干文化交流论坛",
        category="internal_positive",
        minimum=35,
        maximum=49,
        expected_rule="internal 前置边界题材及 Tier 4-C（文化交流论坛）",
        failure_analysis=(
            "前置判定要求文化交流论坛直接落入 Tier 4-C，但 Tier 4-C 又规定北京高校实质主办时改按 Tier 3-F；"
            "本文由首都师范大学主办，当前文字本身就把它从期望的 35-49 推向 50-69。75/85 进一步表明模型还把“11 国、40 多所高校、教育部和市教委领导致辞”误套到 Tier 2-E，尽管核心议题是文化与区域国别研究。"
        ),
    ),
    RegressionCase(
        article_id="7631724657380557363",
        label="300余家国企招聘岗位上线",
        category="internal_positive",
        minimum=0,
        maximum=19,
        expected_rule="internal Tier 6-D（招聘与招考公告）",
        failure_analysis=(
            "Tier 6-D 对具体岗位招聘公告有明确排除且冲突规则要求先判 Tier 6；"
            "75 分说明“教育部联合发布、国家大学生就业服务平台”仍被误套为 Tier 2-B 权威教育治理信息或 Tier 1-C 国家部委政策，排除优先级没有被稳定执行。"
        ),
    ),
    RegressionCase(
        article_id="tencent:20260618A04KRO00",
        label="2027QS世界大学排名发布",
        category="internal_positive",
        minimum=50,
        maximum=69,
        expected_rule="internal Tier 3-H（含北京高校的排名、榜单与评估）",
        failure_analysis=(
            "Tier 3-H 和 Tier 6-B 的例外都明确把含北京高校的第三方排名固定在 50-69；"
            "0/15/19 表明模型仍误走 Tier 6-B，75/85 则误走 Tier 2-F 或更高档，专门条款没有形成稳定裁定。"
        ),
    ),
)


STATIC_ASSESSMENTS: dict[tuple[str, str], tuple[str, str]] = {
    ("internal_positive", "体育"): (
        "有",
        "前置判定明确将青少年体育和体教融合排除出边界题材；Tier 1-D 与 Tier 3-D 再按市级覆盖和项目规模分档，Tier 6-C 只处理无教育实质的普通体育。",
    ),
    ("internal_positive", "科技"): (
        "有",
        "前置判定和 Tier 6-C 明确规定仅出现科技不构成教育实质；Tier 2-E 要求全国性重大活动，Tier 3-B 要求北京高校是科研成果主体。",
    ),
    ("internal_positive", "科研"): (
        "有",
        "Tier 1-G 处理市级决策部署中的高校科研要求，Tier 3-B 处理北京高校作为成果主体的科研突破；Tier 4-C/Tier 5-A 中的“科研”只是说明市教委核心业务边界，不是独立提档条件。",
    ),
    ("internal_positive", "文化"): (
        "有",
        "Tier 4-C 的主体例外把北京学校、高校或教育主管部门实质主办的活动送回 Tier 1/3；冲突裁定再规定排除优先、其余取最高档。",
    ),
    ("internal_positive", "论坛"): (
        "冲突",
        "前置判定要求文化交流论坛直接归 Tier 4-C，但 Tier 4-C 又规定北京高校主办时改按 Tier 3-F。首都师范大学主办的定点文章期望 35-49，与这条例外的 50-69 落点直接冲突；Tier 2-E 还会被国际规模表述误触发。",
    ),
    ("internal_positive", "培训"): (
        "有",
        "前置判定与 Tier 4-C 将社会机构短期职业培训固定在边界档，并以教育主管部门、北京学校或学历职业教育主体作为升级例外。",
    ),
    ("internal_positive", "人才"): (
        "部分",
        "已明确单独出现人才不触发教育实质，但 Tier 1-E、Tier 2-D/E、Tier 3-D/F 等仍依赖主体与规模判断；取最高档规则存在，跨题材命中时仍需模型先正确识别主体。",
    ),
    ("internal_positive", "思政"): (
        "有",
        "Tier 2-C 与 Tier 3-C 以区级覆盖和单校范围分档，前置判定将思政教育明确视为教育实质。",
    ),
    ("internal_positive", "青少年"): (
        "有",
        "前置判定排除青少年体育的边界归类，Tier 1-D/Tier 3-D 按市级覆盖规模分档，Tier 2-C 另处理区级思政艺术育人。",
    ),
    ("internal_positive", "健康"): (
        "有",
        "Tier 1-D/Tier 3-D 的健康指学生和青少年身心健康并按市级覆盖分档；老年健康教育固定 Tier 4-C，普通市民健康固定 Tier 5-A。",
    ),
    ("internal_positive", "招生"): (
        "有",
        "前置判定把招生列为教育实质；Tier 6-D 只排除具体招聘招考公告，Tier 2-G 明确区分高校公共参观与招生开放日。",
    ),
    ("internal_positive", "教师"): (
        "部分",
        "前置判定确保教师题材具有教育实质，但 Tier 1-F、Tier 5-B 等仍需依靠系统性工作与个人报道主旨区分；已有条款，缺少一句集中裁定。",
    ),
    ("internal_positive", "治理"): (
        "有",
        "主体优先规则和冲突裁定明确市教委权威治理信息优先 Tier 2，普通治理借鉴按具体主体、规模和对应条目取最高档。",
    ),
    ("internal_positive", "创新"): (
        "有",
        "前置判定和 Tier 6-C 明确单独出现创新不构成教育实质；具体教育创新再按政策主体、学校主体和规模进入对应档。",
    ),
    ("external_positive", "培训"): (
        "有",
        "前置判定与 Tier 3-E 明确把职业技能培训排除；Tier 2-B 仅适用于具有明确教育主题和行业价值的专业培训。",
    ),
    ("external_positive", "人才"): (
        "有",
        "前置判定明确经济、产业、就业等领域即使出现人才或培训也直接低分；只有教育主体的人才培养才进入 Tier 1/2。",
    ),
    ("external_positive", "治理"): (
        "有",
        "前置判定要求治理创新的实施主体或对象必须是教育系统、学校或师生，并明确非教育治理创新不得进入 Tier 1-C。",
    ),
    ("external_positive", "创新"): (
        "有",
        "前置判定明确非教育领域创新不得进入评分范围，Tier 1-C 进一步限定教育系统主体或学校师生对象。",
    ),
}


UNSTABLE_DIAGNOSES: dict[tuple[str, str], str] = {
    ("A", "7575087565024379392"): (
        "external Tier 3-A（单校一般活动） ↔ Tier 2-A（局部课程与教学实践）；同一校本教研被交替理解为例行活动或有启发性的教学实践"
    ),
    ("A", "7584376512065929737"): (
        "external Tier 3-A（单校常规检查） ↔ Tier 2-A/2-C（局部教育实践或一般权威信息）；学校自发稿中的教学管理细节触发了不同判断"
    ),
    ("A", "7585425820647277066"): (
        "internal Tier 6-C（纯文化与考古论坛） ↔ Tier 4-C（在京文化交流论坛边界题材）"
    ),
    ("A", "7632134103197811254"): (
        "internal Tier 6-C（戏曲展演与文化传播） ↔ Tier 4-C（在京文化交流边界题材）；20 分是中间的 Tier 5-A"
    ),
    ("A", "7636001703149486619"): (
        "external Tier 3-E（科普活动明确排除） ↔ Tier 2-A/2-B（把工业研学游误作教育实践或教育活动）"
    ),
    ("A", "jyb:/rmtzgjyb/202606/t20260611_2111490047"): (
        "internal Tier 6-B（多地/京外报道） ↔ Tier 3-G（全国高中学位供给的教育观察）；正文包含北京案例但主体是全国性教育分析"
    ),
    ("A", "qianlong:2026/0627/8689051"): (
        "internal Tier 6-C（市民生态健康科普） ↔ Tier 4-C（把标题中的“专家科普”误当在京科普活动）"
    ),
    ("A", "tencent:20260310A02U9700"): (
        "internal Tier 6-C（企业 AI 与就业政策） ↔ Tier 4-C（把企业职业技能培训表述误作边界教育活动）"
    ),
    ("A", "tencent:20260607A02IBY00"): (
        "external Tier 3-D/3-C（高考当天实用事实与低分析信息） ↔ Tier 2-C（一般权威教育信息）"
    ),
    ("A", "tencent:20260710A08E3D00"): (
        "internal Tier 5-C（非学校主体的青年科研实习、无明确对应项） ↔ Tier 4-A（在京小范围活动）；阈值 30 把相邻判断转成不同决策"
    ),
    ("A", "chinanews:/gn/shipin/2026/03-07/news1048284"): (
        "external Tier 3-C（正文只有标题、缺乏实质分析） ↔ Tier 1-B（政府工作报告中的重大教育政策）；45 分是中间的 Tier 2-C"
    ),
    ("A", "7576077820816884262"): (
        "internal Tier 6-C（院士增选属于科技人才而非教育） ↔ Tier 3-B（因北京大学教授和科研表述误套高校科研成果）"
    ),
    ("A", "7648848244943962659"): (
        "internal Tier 5-C 同一条款内部：25-29 不过 ↔ 30-34 通过；验收阈值 30 切穿 Tier 5 的 20-34 区间，并非两个 Tier 条款冲突"
    ),
    ("A", "7664113108658487860"): (
        "internal Tier 5-C（教育相关但无明确北京主体） ↔ Tier 4-A（在京小范围美育研学）"
    ),
    ("A", "7665280166154781238"): (
        "internal Tier 6-C（国家体育规划主体非教育） ↔ Tier 1-C/1-D（因青少年体育、体育教育和人才培养表述误套国家教育政策或市级体教融合）"
    ),
    ("A", "7671187122530812457"): (
        "internal Tier 6-C/Tier 5-A（城市便民设施） ↔ Tier 4-A（因位于小学校门口而被当作小范围校园安全实践）"
    ),
    ("D", "7571345639725662763"): (
        "external Tier 3-A（单校课堂展示） ↔ Tier 2-A（具有方法细节的局部教学实践）"
    ),
    ("D", "7576077820816884262"): (
        "internal Tier 6-C（科技人才报道） ↔ Tier 2-F/3-B（把权威媒体科技分析或北大教授当作北京教育系统总结/高校科研成果）"
    ),
    ("D", "7599799731375620662"): (
        "external Tier 3-D（科学家个人事迹） ↔ Tier 1-A/1-D（因导师、学生和奖学金内容误套教育评论或系统性教育成果）"
    ),
    ("D", "7614650800782017060"): (
        "external 前置非教育主体/Tier 3-C（区域人才与就业发展） ↔ Tier 1-B/1-C（把省级青年政策误作重大教育政策或教育治理创新）"
    ),
    ("D", "7648848244943962659"): (
        "internal Tier 6-B（非北京主体的全国高考外媒综述） ↔ Tier 2-F（因北京考点和权威教育内容误作北京教育系统性报道）"
    ),
    ("D", "7664113108658487860"): (
        "internal Tier 6-B/6-C（京外学校参加商业舞剧研学） ↔ Tier 3-C（误作北京单校美育活动）"
    ),
    ("D", "7665280166154781238"): (
        "internal Tier 6-C（体育主管部门的全国体育规划） ↔ Tier 1-C/1-D（因青少年体育和体育教育表述误作重大教育政策或市级体教融合）"
    ),
    ("D", "7671187122530812457"): (
        "internal Tier 6-C（城市公共空间微改造） ↔ Tier 2-B（因学校门口安全隔离表述误作市级教育治理权威信息）"
    ),
    ("D", "chinadaily:/a/202512/03/WS69301868a310942cc4994cc5"): (
        "internal Tier 6-C（科研趋势报告与科技论坛） ↔ Tier 3-B/3-F（因北京高校参会和科研报告误作高校科研成果或单校论坛）"
    ),
    ("D", "chinanews:/txy/2026/06-12/10638944"): (
        "internal Tier 6-B（京外毕节职业院校） ↔ Tier 1-F（北京参与的跨区域教育对口帮扶）；地域排除与对口支援例外未稳定裁定"
    ),
}


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _artifact_path(path: Path) -> Path:
    resolved = path.resolve()
    root = (_REPO_ROOT / "artifacts").resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Output must stay under artifacts/: {path}")
    return resolved


def _prior_article_ids(path: Path) -> set[str]:
    if not path.exists():
        raise FileNotFoundError(f"Prior determinism CSV is missing: {path}")
    article_ids = {row.article_id for row in baseline._read_csv(path)}
    if len(article_ids) != 30:
        raise RuntimeError(f"Expected 30 prior articles, found {len(article_ids)}")
    return article_ids


def _fetch_sample(
    sample_size: int,
    seed: str,
    excluded_ids: set[str],
) -> list[dict[str, Any]]:
    if sample_size % 10:
        raise ValueError("Sample size must be divisible by 10")
    per_stratum = sample_size // 10
    with v2_acceptance._connect_read_only() as conn, conn.cursor() as cur:
        cur.execute(SAMPLE_QUERY, (sorted(excluded_ids), seed, per_stratum))
        rows = [dict(row) for row in cur.fetchall()]
    expected = {
        (base_category, score_band): per_stratum
        for base_category in ("internal", "external")
        for score_band in range(5)
    }
    actual = Counter(
        (str(row.get("base_category")), int(row.get("score_band") or 0))
        for row in rows
    )
    if len(rows) != sample_size or actual != expected:
        raise RuntimeError(
            f"Stratified sample is incomplete: rows={len(rows)}, strata={actual}"
        )
    return rows


def _fetch_regression_candidates() -> tuple[
    dict[str, tuple[ExternalFilterCandidate, float]],
    int,
    bool,
    str,
]:
    database_ids = [case.article_id for case in REGRESSION_CASES if not case.external_page]
    rows = v2_acceptance._fetch_same_rows(database_ids)
    by_id: dict[str, tuple[ExternalFilterCandidate, float]] = {}
    for row in rows:
        candidate = baseline._candidate_from_row(row)
        category = str(row.get("category") or "")
        expected = next(case for case in REGRESSION_CASES if case.article_id == candidate.article_id)
        if category != expected.category:
            raise RuntimeError(
                f"Regression category drift for {candidate.article_id}: {category}"
            )
        by_id[candidate.article_id] = (
            candidate,
            float(row.get("external_importance_score") or 0),
        )
    target, chars, truncated, method = v2_acceptance._fetch_target_article()
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
        common_payload_sha256=baseline._stable_hash(
            baseline._common_payload(payload)
        ),
        request_payload_sha256=baseline._stable_hash(payload),
        payload=payload,
    )


def _build_plans(
    sample_rows: Sequence[Mapping[str, Any]],
    regression_candidates: Mapping[
        str,
        tuple[ExternalFilterCandidate, float],
    ],
    *,
    include_regressions: bool,
) -> list[baseline.InvocationPlan]:
    plans: list[baseline.InvocationPlan] = []
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
    if include_regressions:
        for case in REGRESSION_CASES:
            candidate, stored_score = regression_candidates[case.article_id]
            for repetition in range(1, REGRESSION_REPETITIONS + 1):
                plans.append(
                    _build_plan(
                        candidate,
                        category=case.category,
                        stored_score=stored_score,
                        config_name="D",
                        repetition=repetition,
                    )
                )
    random.Random(f"{SAMPLE_SEED}:invocations").shuffle(plans)
    baseline._assert_only_intended_variables(plans)
    keys = {(plan.article_id, plan.config_name, plan.repetition) for plan in plans}
    if len(keys) != len(plans):
        raise RuntimeError("Invocation plan contains duplicate logical slots")
    return plans


def _validate_payloads(plans: Sequence[baseline.InvocationPlan]) -> dict[str, Any]:
    settings = get_settings()
    a_reference = baseline._payload_for_config("<PROMPT>", "A", FIXED_PROVIDER)
    skeletons: dict[str, set[str]] = defaultdict(set)
    for plan in plans:
        skeleton = {
            **{key: value for key, value in plan.payload.items() if key != "messages"},
            "messages": [{"role": "user", "content": "<PROMPT>"}],
        }
        skeletons[plan.config_name].add(baseline._stable_hash(skeleton))
        if plan.payload.get("model") != settings.llm_external_filter_model:
            raise RuntimeError(f"Model drift in {plan.config_name}")
        if plan.payload.get("temperature") != 0.0:
            raise RuntimeError(f"Temperature drift in {plan.config_name}")
        if plan.config_name == "A":
            if "provider" in plan.payload:
                raise RuntimeError("A unexpectedly fixes a provider")
            if plan.payload.get("reasoning") != a_reference.get("reasoning"):
                raise RuntimeError("A reasoning differs from production")
        elif plan.config_name == "D":
            if plan.payload.get("reasoning") != {"effort": "none"}:
                raise RuntimeError("D reasoning is not disabled")
            if plan.payload.get("provider") != {
                "only": [FIXED_PROVIDER],
                "allow_fallbacks": False,
            }:
                raise RuntimeError("D provider is not fixed")
        else:
            raise RuntimeError(f"Unexpected config: {plan.config_name}")
    counts = {name: len(values) for name, values in sorted(skeletons.items())}
    if counts != {"A": 1, "D": 1}:
        raise RuntimeError(f"Payload skeleton drift: {counts}")
    return {
        "model": settings.llm_external_filter_model,
        "a_reasoning": a_reference.get("reasoning"),
        "skeleton_hash_counts": counts,
    }


def _scan_prompt(path: Path) -> dict[str, list[str]]:
    locations: dict[str, set[str]] = {keyword: set() for keyword in KEYWORDS}
    current_section = "其他"
    current_tier: Optional[str] = None
    current_item: Optional[str] = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        section_match = re.search(r"【([^】]+)】", line)
        if section_match:
            current_section = section_match.group(1)
            current_tier = None
            current_item = None
        tier_match = re.match(r"Tier\s+(\d+):", line)
        if tier_match:
            current_tier = tier_match.group(1)
            current_item = None
        item_match = re.match(r"\*\s+([A-Z])\.", line)
        if item_match and current_tier:
            current_item = item_match.group(1)
        boundary_match = re.match(r"\*\*（([一二三四五六七八九十]+)）", line)
        boundary = boundary_match.group(1) if boundary_match else None
        if current_tier:
            location = f"Tier {current_tier}-{current_item}" if current_item else f"Tier {current_tier}"
        elif boundary:
            location = f"{current_section}-{boundary}"
        else:
            location = current_section
        for keyword in KEYWORDS:
            if keyword in line:
                locations[keyword].add(location)
    return {keyword: sorted(values) for keyword, values in locations.items()}


def _static_scan() -> dict[str, dict[str, list[str]]]:
    return {
        prompt_name: _scan_prompt(path)
        for prompt_name, path in PROMPT_PATHS.items()
    }


def _tier_count(locations: Sequence[str]) -> int:
    return len(
        {
            match.group(1)
            for location in locations
            if (match := re.match(r"Tier\s+(\d+)", location))
        }
    )


def _group_scores(
    calls: Sequence[baseline.CallResult],
    article_ids: set[str],
    config_name: str,
    repetitions: int,
) -> dict[str, list[int]]:
    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in calls:
        if row.article_id not in article_ids or row.config_name != config_name:
            continue
        if row.status != "ok" or row.parsed_score is None:
            continue
        grouped[row.article_id].append((row.repetition, int(row.parsed_score)))
    result = {
        article_id: [score for _, score in sorted(values)]
        for article_id, values in grouped.items()
    }
    if len(result) != len(article_ids) or any(
        len(scores) != repetitions for scores in result.values()
    ):
        raise RuntimeError(
            f"Incomplete {config_name} scores: articles={len(result)}, expected={len(article_ids)}"
        )
    return result


def _threshold(category: str) -> int:
    return 30 if category == "internal_positive" else 35


def _is_decision_stable(category: str, scores: Sequence[int]) -> bool:
    decisions = {score >= _threshold(category) for score in scores}
    return len(decisions) == 1


def _score_tier(category: str, score: int) -> str:
    if category == "internal_positive":
        ranges = ((85, "Tier 1"), (70, "Tier 2"), (50, "Tier 3"), (35, "Tier 4"), (20, "Tier 5"), (0, "Tier 6"))
    else:
        ranges = ((70, "Tier 1"), (35, "Tier 2"), (0, "Tier 3"))
    return next(label for minimum, label in ranges if score >= minimum)


def _default_unstable_diagnosis(
    category: str,
    scores: Sequence[int],
) -> str:
    low = min(scores)
    high = max(scores)
    low_tier = _score_tier(category, low)
    high_tier = _score_tier(category, high)
    if low_tier == high_tier:
        return (
            f"{low_tier} 内部阈值两侧（{low}-29 不过 ↔ 30-{high} 通过）；"
            "需结合正文复核该 Tier 的具体条目"
        )
    return f"{low_tier} ↔ {high_tier}；需结合正文复核具体条目"


def _histogram(scores: Sequence[int]) -> list[tuple[str, int]]:
    bins: list[tuple[str, int]] = []
    for start in range(0, 100, 10):
        end = 100 if start == 90 else start + 9
        count = sum(start <= score <= end for score in scores)
        bins.append((f"{start}-{end}", count))
    return bins


def _bar(count: int, maximum: int) -> str:
    width = round(count / maximum * 24) if maximum else 0
    return "█" * width


def _escape(value: str) -> str:
    return " ".join((value or "").split()).replace("|", "\\|")


def _render_report(
    *,
    sample_rows: Sequence[Mapping[str, Any]],
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
            "content": str(row.get("content_markdown") or ""),
        }
        for row in sample_rows
    }
    grouped = {
        config_name: _group_scores(
            calls,
            sample_ids,
            config_name,
            SAMPLE_REPETITIONS,
        )
        for config_name in ("A", "D")
    }
    stable_counts: dict[str, int] = {}
    unstable_ids: dict[str, list[str]] = {}
    for config_name in ("A", "D"):
        unstable_ids[config_name] = [
            article_id
            for article_id, scores in grouped[config_name].items()
            if not _is_decision_stable(metadata[article_id]["category"], scores)
        ]
        stable_counts[config_name] = len(sample_ids) - len(unstable_ids[config_name])

    regression_ids = {case.article_id for case in REGRESSION_CASES}
    regression_scores = _group_scores(
        calls,
        regression_ids,
        "D",
        REGRESSION_REPETITIONS,
    )
    regression_passed: dict[str, bool] = {}
    for case in REGRESSION_CASES:
        scores = regression_scores[case.article_id]
        regression_passed[case.article_id] = all(
            case.minimum <= score <= case.maximum for score in scores
        )

    a_passed = stable_counts["A"] >= 85
    all_regressions_passed = all(regression_passed.values())
    overall_passed = a_passed and all_regressions_passed
    statuses = Counter(row.status for row in calls)
    providers = {
        config_name: Counter(
            row.response_provider or "(missing)"
            for row in calls
            if row.config_name == config_name and row.article_id in sample_ids
        )
        for config_name in ("A", "D")
    }
    models = Counter(row.response_model or "(missing)" for row in calls)
    strata = Counter(
        (str(row.get("base_category")), int(row.get("score_band") or 0))
        for row in sample_rows
    )

    candidate_conflicts = [
        (prompt_name, keyword, locations)
        for prompt_name, keyword_map in static_scan.items()
        for keyword, locations in keyword_map.items()
        if _tier_count(locations) >= 3
    ]
    attention_candidates = [
        item
        for item in candidate_conflicts
        if STATIC_ASSESSMENTS.get((item[0], item[1]), ("无", ""))[0] != "有"
    ]

    lines = [
        "# 四分类正面提示词 v3 验收报告",
        "",
        "## 技术结论",
        "",
        f"**验收结论：{'通过' if overall_passed else '不通过'}。** A 组决策稳定 "
        f"{stable_counts['A']}/100（上线目标 ≥85），因此生产配置主指标"
        f"{'通过' if a_passed else '不通过'}；8 条定点回归通过 "
        f"{sum(regression_passed.values())}/8。D 组决策稳定 {stable_counts['D']}/100，"
        "仅作为低噪诊断证据。",
        "",
        f"- 调用完整性：计划 640 次，CSV 640 行；状态分布 {dict(statuses)}，响应 model {dict(models)}",
        f"- 提示词版本：internal_positive=`{versions['internal_positive']}`、external_positive=`{versions['external_positive']}`；internal_negative=`{versions['internal_negative']}`、external_negative=`{versions['external_negative']}`（负面提示词未调用）",
        f"- 静态交叉检查：{len(candidate_conflicts)} 个“同一提示词内跨 ≥3 个 Tier”的关键词；其中 {len(attention_candidates)} 个为部分裁定或与验收目标冲突，另发现 1 处前置判定结构矛盾",
        "- 本报告不使用“逐次分数完全一致”作为验收指标；v3 允许同一档位内按理由取不同整数",
        "",
        "## A 组达到上线所需的决策稳定性",
        "",
        "决策稳定定义：同一文章同一配置的 3 次分数，在该类别阈值下全部给出相同的通过/不通过结论。internal_positive 使用本次验收阈值 30，external_positive 使用 35；30 只用于本报告计算，没有修改生产配置。",
        "",
        "| 配置 | 决策稳定 | 备注 |",
        "|---|---:|---|",
        f"| A 组 | {stable_counts['A']}/100 | 生产配置；上线目标 ≥85，{'通过' if a_passed else '不通过'} |",
        f"| D 组 | {stable_counts['D']}/100 | 固定 Alibaba FP8、关闭 reasoning 的低噪环境 |",
        "",
        "### 决策不稳定文章",
        "",
    ]
    for config_name in ("A", "D"):
        lines.extend(
            [
                f"#### {config_name} 组：{len(unstable_ids[config_name])} 条",
                "",
            ]
        )
        if not unstable_ids[config_name]:
            lines.append("无。")
            lines.append("")
            continue
        lines.extend(
            [
                "| article_id | 标题 | 类别 | 3 次分数 | 决策 | 档位与条款判断 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for article_id in unstable_ids[config_name]:
            info = metadata[article_id]
            scores = grouped[config_name][article_id]
            threshold = _threshold(info["category"])
            decisions = ["过" if score >= threshold else "不过" for score in scores]
            diagnosis = UNSTABLE_DIAGNOSES.get(
                (config_name, article_id),
                _default_unstable_diagnosis(info["category"], scores),
            )
            lines.append(
                f"| {article_id} | {_escape(info['title'])} | {info['category']} | "
                f"{'/'.join(map(str, scores))} | {'/'.join(decisions)} | {_escape(diagnosis)} |"
            )
        lines.append("")

    lines.extend(
        [
            "### 分数分布与辅助指标",
            "",
            "直方图使用 10 分等宽分档；条形只帮助观察形状，精确条数以括号数字为准。每组分母均为 100 篇 × 3 次 = 300 次。",
            "",
            "| 分数段 | A 组 | D 组 |",
            "|---|---|---|",
        ]
    )
    histograms: dict[str, list[tuple[str, int]]] = {}
    for config_name in ("A", "D"):
        scores = [score for values in grouped[config_name].values() for score in values]
        histograms[config_name] = _histogram(scores)
    max_hist = max(count for histogram in histograms.values() for _, count in histogram)
    for index, (label, _) in enumerate(histograms["A"]):
        a_count = histograms["A"][index][1]
        d_count = histograms["D"][index][1]
        lines.append(
            f"| {label} | `{_bar(a_count, max_hist)}` {a_count} | "
            f"`{_bar(d_count, max_hist)}` {d_count} |"
        )

    lines.extend(
        [
            "",
            "| 配置 | 类别 | 平均分 | 逐篇极差中位数 | 0-19 取值数 | 20-39 取值数 | 40-59 取值数 | 60-79 取值数 | 80-100 取值数 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for config_name in ("A", "D"):
        for category in ("internal_positive", "external_positive"):
            ids = [aid for aid in sample_ids if metadata[aid]["category"] == category]
            values = [score for aid in ids for score in grouped[config_name][aid]]
            ranges = [max(grouped[config_name][aid]) - min(grouped[config_name][aid]) for aid in ids]
            distinct_by_band = []
            for low, high in ((0, 19), (20, 39), (40, 59), (60, 79), (80, 100)):
                distinct_by_band.append(len({score for score in values if low <= score <= high}))
            lines.append(
                f"| {config_name} | {category} | {mean(values):.2f} | {median(ranges):.1f} | "
                + " | ".join(map(str, distinct_by_band))
                + " |"
            )

    lines.extend(
        [
            "",
            "## 8 条定点回归",
            "",
            f"**结果：{sum(regression_passed.values())}/8 通过。** 任一不通过项的 5 次原始输出附在本节表格之后。",
            "",
            "| 文章 | 类别 | 期望 | D 组 5 次 | 结果 | 对应规则 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for case in REGRESSION_CASES:
        scores = regression_scores[case.article_id]
        lines.append(
            f"| {_escape(case.label)} | {case.category} | {case.expectation} | "
            f"{'/'.join(map(str, scores))} | {'通过' if regression_passed[case.article_id] else '**不通过**'} | "
            f"{_escape(case.expected_rule)} |"
        )
    lines.extend(
        [
            "",
            f"市政府常务会议原文仍未在库中命中；脚本沿用上次方式对[北京日报客户端原文]({v2_acceptance.TARGET_URL})执行 HTTP GET，从 `{target_fetch_method}` 抽取正文。正文字数 {target_chars}，1500 字截断：{'是' if target_truncated else '否'}。",
        ]
    )
    failed_cases = [case for case in REGRESSION_CASES if not regression_passed[case.article_id]]
    if failed_cases:
        lines.extend(["", "### 未通过项原始输出与规则判断", ""])
        for case in failed_cases:
            case_calls = sorted(
                [row for row in calls if row.article_id == case.article_id and row.config_name == "D"],
                key=lambda row: row.repetition,
            )
            scores = regression_scores[case.article_id]
            lines.extend(
                [
                    f"#### {_escape(case.label)}：{'/'.join(map(str, scores))}",
                    "",
                    f"判断：未稳定落入 `{case.expectation}`。{case.failure_analysis or ('预期依据是 ' + case.expected_rule + '；偏离说明模型仍可能在相邻排除/升级条款之间切换。')}",
                    "",
                ]
            )
            for row in case_calls:
                lines.extend(
                    [
                        f"第 {row.repetition} 次（parsed={row.parsed_score}）：",
                        "",
                        "```text",
                        row.raw_output,
                        "```",
                        "",
                    ]
                )

    lines.extend(
        [
            "## 题材关键词交叉检查",
            "",
            "**额外发现一处不依赖关键词计数的结构矛盾：** internal_positive 的【第一步】开头仍写“先做一个二值判断”，紧接着又要求“判定结果分三种，不要只做是/否二选一”。这与 v3 的三分判定目标直接冲突，应删除“二值判断”残留措辞。",
            "",
            "扫描对象仅为当前 internal_positive 与 external_positive v3 文件。位置按所在 Tier 条目去重；“跨越档位数”只统计同一提示词内不同 Tier，不把前置判定和冲突裁定算作 Tier。",
            "",
            "| 提示词 | 关键词 | 出现位置列表 | 跨越档位数 |",
            "|---|---|---|---:|",
        ]
    )
    for prompt_name in ("internal_positive", "external_positive"):
        for keyword in KEYWORDS:
            locations = list(static_scan[prompt_name][keyword])
            lines.append(
                f"| {prompt_name} | {keyword} | {_escape('；'.join(locations) if locations else '—')} | {_tier_count(locations)} |"
            )
    lines.extend(
        [
            "",
            "### 跨越 3 个及以上档位的候选冲突点",
            "",
        ]
    )
    if not candidate_conflicts:
        lines.append("无。")
    else:
        for prompt_name, keyword, locations in candidate_conflicts:
            status, assessment = STATIC_ASSESSMENTS.get(
                (prompt_name, keyword),
                ("无", "未发现针对这些跨档位置的集中裁定规则；需要人工补充主体、规模或排除优先级。"),
            )
            lines.extend(
                [
                    f"- **{prompt_name} / {keyword}：裁定状态为{status}。** {assessment}（位置：{'、'.join(locations)}）",
                ]
            )

    lines.extend(
        [
            "",
            "## 样本、参数与可复现性",
            "",
            f"- 抽样种子：`{SAMPLE_SEED}`。从已完成 external-filter 的正面文章中抽样，internal_positive 与 external_positive 各 50 条；原分数 0-19、20-39、40-59、60-79、80-100 每个“类别 × 分档”精确 10 条。实际分层：{dict(strata)}。",
            "- 样本不沿用此前 30 条：从上一轮 determinism CSV 读取其 article_id 并在 SQL 中全部排除；7 条库内定点回归也包含在该排除集合中，因此与 100 条主样本无重叠。排序使用 `md5(article_id || seed)`，同库快照下可复现。",
            f"- A/D 共同参数：生产 `build_prompt`（默认正文 1500 字）、model=`{payload_check['model']}`、temperature=0、timeout={timeout}s、retries={retries}、concurrency={CONCURRENCY}，并复用上一轮 `_invoke` 与生产 `parse_external_filter_score`。",
            f"- 唯一指定差异：A 不设置 provider，reasoning 使用生产值 `{payload_check['a_reasoning']}`；D 设置 provider.only=[`{FIXED_PROVIDER}`]、allow_fallbacks=false、reasoning.effort=none。去除实际 prompt 后 payload skeleton 数量={payload_check['skeleton_hash_counts']}；每篇 A/D 的 prompt SHA 与 common payload SHA 均相同。",
            f"- 实际路由：A={dict(providers['A'])}；D={dict(providers['D'])}。",
            f"- CSV：`{csv_path.as_posix()}`，字段顺序与前两轮 `CSV_FIELDS` 完全一致；所有逻辑槽位保留 status/error，不静默丢弃。",
            "",
            "## 限制与下一步",
            "",
            "- 这是一次分层随机样本上的重复调用实验，描述当前模型与当前路由的稳定性，不证明未来供应商路由或模型版本变化后仍保持同一水平。",
            "- 原分数分层只用于扩大覆盖面，不代表生产流量的自然分布，因此直方图和平均分不应解释为线上总体分布。",
            "- 若 A 组未达 85/100，或定点回归仍失败，应优先修订报告列出的具体冲突条款，再用相同种子复测；本任务未修改提示词、阈值或生产配置。",
            "",
            f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat()}",
        ]
    )
    return "\n".join(lines) + "\n"


def _pilot_summary(
    rows: Sequence[Mapping[str, Any]],
    calls: Sequence[baseline.CallResult],
) -> dict[str, Any]:
    article_ids = {str(row.get("article_id") or "") for row in rows}
    categories = {
        str(row.get("article_id") or ""): str(row.get("category") or "")
        for row in rows
    }
    output: dict[str, Any] = {"sample_size": len(rows), "call_count": len(calls)}
    for config_name in ("A", "D"):
        grouped = _group_scores(calls, article_ids, config_name, SAMPLE_REPETITIONS)
        output[config_name] = {
            "decision_stable": sum(
                _is_decision_stable(categories[article_id], scores)
                for article_id, scores in grouped.items()
            ),
            "articles": len(grouped),
        }
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time read-only acceptance test for positive importance prompts v3"
    )
    parser.add_argument("--sample-size", type=_positive_int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", default=SAMPLE_SEED)
    parser.add_argument("--prior-csv", type=Path, default=PRIOR_CSV_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--concurrency", type=_positive_int, default=CONCURRENCY)
    parser.add_argument("--retries", type=_positive_int)
    parser.add_argument("--timeout", type=_positive_int)
    parser.add_argument("--pilot", action="store_true")
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
    sample_size = PILOT_SAMPLE_SIZE if args.pilot else args.sample_size
    include_regressions = not args.pilot
    if not args.pilot and sample_size != DEFAULT_SAMPLE_SIZE:
        raise ValueError("Full acceptance requires exactly 100 sample articles")
    if args.seed != SAMPLE_SEED:
        raise ValueError(f"Acceptance requires seed={SAMPLE_SEED}")

    settings = get_settings()
    retries = args.retries or settings.external_filter_max_retries
    timeout = args.timeout or settings.llm_external_filter_timeout
    report_path = _artifact_path(args.report)
    csv_path = _artifact_path(args.csv)
    versions = load_prompt_versions()
    if versions != {
        "external_positive": "v3",
        "external_negative": "v1",
        "internal_positive": "v3",
        "internal_negative": "v1",
    }:
        raise RuntimeError(f"Unexpected prompt versions: {versions}")

    prior_ids = _prior_article_ids(args.prior_csv)
    excluded_ids = prior_ids | {
        case.article_id for case in REGRESSION_CASES if not case.external_page
    }
    sample_rows = _fetch_sample(sample_size, args.seed, excluded_ids)
    static_scan = _static_scan()
    regression_candidates: dict[
        str,
        tuple[ExternalFilterCandidate, float],
    ] = {}
    target_chars = 0
    target_truncated = False
    target_fetch_method = "not fetched during pilot"
    if include_regressions:
        (
            regression_candidates,
            target_chars,
            target_truncated,
            target_fetch_method,
        ) = _fetch_regression_candidates()
    plans = _build_plans(
        sample_rows,
        regression_candidates,
        include_regressions=include_regressions,
    )
    payload_check = _validate_payloads(plans)
    diagnostics = {
        "mode": "pilot" if args.pilot else "full",
        "sample_seed": SAMPLE_SEED,
        "sample_size": len(sample_rows),
        "planned_calls": len(plans),
        "strata": {
            f"{base_category}:{score_band}": count
            for (base_category, score_band), count in sorted(
                Counter(
                    (
                        str(row.get("base_category")),
                        int(row.get("score_band") or 0),
                    )
                    for row in sample_rows
                ).items()
            )
        },
        "versions": versions,
        "payload_check": payload_check,
        "static_candidates": sum(
            _tier_count(locations) >= 3
            for keyword_map in static_scan.values()
            for locations in keyword_map.values()
        ),
    }
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
    if args.dry_run:
        return 0

    expected = {(plan.article_id, plan.config_name, plan.repetition) for plan in plans}
    if args.render_only:
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV does not exist: {csv_path}")
        calls = baseline._read_csv(csv_path)
        actual = {(row.article_id, row.config_name, row.repetition) for row in calls}
        if actual != expected:
            raise RuntimeError(
                f"Existing CSV does not match plans: expected={len(expected)}, actual={len(actual)}"
            )
    else:
        calls = v2_acceptance._run_with_resume(
            plans,
            concurrency=args.concurrency,
            retries=retries,
            timeout=timeout,
            csv_path=csv_path,
        )
    if any(row.status != "ok" or row.parsed_score is None for row in calls):
        failures = Counter(row.status for row in calls if row.status != "ok")
        raise RuntimeError(f"Experiment contains incomplete calls: {failures}")

    if args.pilot:
        print(
            json.dumps(
                _pilot_summary(sample_rows, calls),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    report = _render_report(
        sample_rows=sample_rows,
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
