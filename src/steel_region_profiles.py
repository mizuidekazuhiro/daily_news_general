from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RegionProfile:
    slug: str
    label: str
    output_countries: tuple[str, ...]
    evidence_patterns: tuple[str, ...]
    focus: str

    def matches(self, text: str) -> bool:
        return any(re.search(pattern, str(text or ""), re.IGNORECASE) for pattern in self.evidence_patterns)

    @property
    def scope_prompt(self) -> str:
        countries = ", ".join(self.output_countries)
        return (
            f"Steel-industry Intelligence backfill for {self.label}. "
            f"Track durable events materially connected to operations, markets, policy or projects in {countries}. "
            "Do not treat company nationality alone as geographic evidence. "
            f"Priority themes: {self.focus}."
        )


REGION_PROFILES: dict[str, RegionProfile] = {
    "india": RegionProfile(
        slug="india",
        label="India",
        output_countries=("India",),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+india\b",
            r"\bindia(?:n)?\s+(?:operations?|business|capacity|production|output|plant|plants|mill|mills|steel|steelmaking|market|demand|supply|project|projects|investment|investments|capex|facility|facilities|mine|mines|mining|scrap|imports?|exports?|policy|tariff|dut(?:y|ies)|government|customers?|automotive|infrastructure|expansion|manufacturing|sector|industry)\b",
            r"\b(?:odisha|orissa|jharkhand|gujarat|maharashtra|andhra\s+pradesh|karnataka|punjab|chhattisgarh|west\s+bengal|tamil\s+nadu|uttar\s+pradesh|telangana|rajasthan|jamshedpur|kalinganagar|hazira|vijayanagar|dolvi|salem|ludhiana|bokaro|bhilai|rayalaseema|rajayyapeta|anakapalli|keonjhar|paradeep|dhinkia|gadchiroli)\b",
            r"インド(?:国内|事業|市場|生産|製鉄|鉄鋼|工場|投資|設備|能力|政策|需要|供給)",
        ),
        focus="new capacity, brownfield expansion, JVs/M&A, EAF/green steel, coking coal and iron ore, trade policy and project execution",
    ),
    "japan": RegionProfile(
        slug="japan",
        label="Japan",
        output_countries=("Japan",),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+japan\b",
            r"\bjapan(?:ese)?\s+(?:operations?|steel|steelmaking|steelmaker|steelmakers|plant|plants|mill|mills|market|demand|supply|capacity|production|output|project|projects|investment|investments|capex|scrap|imports?|exports?|policy|tariff|government|industry|sector)\b",
            r"\b(?:kimitsu|muroran|wakayama|oita|hirohata|kashima|chiba|kurashiki|fukuyama|yawata|kawasaki|toyohashi|himeji)\b",
            r"日本(?:国内|製鉄|鉄鋼|製鋼|電炉|高炉|工場|設備|生産|能力|投資|市場|需要|供給|政策|スクラップ|輸入|輸出)",
        ),
        focus="EAF and BF/BOF restructuring, mill closure/consolidation, capacity and capex, scrap availability, domestic demand, imports and decarbonisation",
    ),
    "asean": RegionProfile(
        slug="asean",
        label="ASEAN",
        output_countries=("Vietnam", "Thailand", "Indonesia", "Malaysia", "Philippines"),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+(?:vietnam|thailand|indonesia|malaysia|the\s+philippines|philippines)\b",
            r"\b(?:vietnamese|thai|indonesian|malaysian|philippine)\s+(?:steel|steelmaker|steelmakers|plant|plants|mill|mills|market|capacity|production|project|investment|imports?|exports?|policy|industry|sector)\b",
            r"\b(?:asean|southeast\s+asia|south-east\s+asia)\s+(?:steel|market|capacity|demand|supply|imports?|exports?|policy|industry|sector)\b",
            r"(?:ベトナム|タイ|インドネシア|マレーシア|フィリピン)(?:国内|鉄鋼|製鉄|工場|設備|生産|能力|投資|市場|需要|供給|政策|輸入|輸出)",
        ),
        focus="greenfield and brownfield capacity, Chinese steel inflows, import safeguards/anti-dumping, EAF projects, construction demand, raw-material sourcing and cross-border JVs",
    ),
    "china": RegionProfile(
        slug="china",
        label="China",
        output_countries=("China",),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+china\b",
            r"\bchinese\s+(?:steel|steelmaker|steelmakers|plant|plants|mill|mills|market|capacity|production|output|exports?|imports?|demand|policy|government|industry|sector)\b",
            r"\bchina(?:'s)?\s+(?:steel|steelmaking|capacity|production|exports?|imports?|demand|market|policy|industry|sector)\b",
            r"中国(?:国内|鉄鋼|製鉄|粗鋼|工場|設備|生産|能力|投資|市場|需要|供給|政策|輸入|輸出)",
        ),
        focus="steel exports, capacity controls and consolidation, production cuts, domestic demand, pricing, trade remedies and decarbonisation",
    ),
    "us": RegionProfile(
        slug="us",
        label="United States",
        output_countries=("United States",),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+(?:the\s+)?(?:united\s+states|u\.s\.|usa)\b",
            r"\b(?:u\.s\.|us|american)\s+(?:steel|steelmaker|steelmakers|plant|plants|mill|mills|market|capacity|production|demand|imports?|exports?|tariff|policy|government|industry|sector)\b",
            r"\b(?:nucor|steel\s+dynamics|cleveland-cliffs|u\.s\.\s+steel)\b.{0,80}\b(?:plant|mill|capacity|investment|project|production|eaf|steel)\b",
            r"米国(?:国内|鉄鋼|製鉄|電炉|工場|設備|生産|能力|投資|市場|需要|供給|関税|政策|輸入|輸出)",
        ),
        focus="EAF investment, Nucor/Steel Dynamics/Cleveland-Cliffs capacity moves, Section 232 and trade policy, automotive demand, scrap and low-carbon steel",
    ),
    "eu": RegionProfile(
        slug="eu",
        label="European Union",
        output_countries=("EU",),
        evidence_patterns=(
            r"\b(?:in|into|across|within|from|to)\s+(?:the\s+)?(?:european\s+union|eu|europe)\b",
            r"\b(?:eu|european)\s+(?:steel|steelmaker|steelmakers|plant|plants|mill|mills|market|capacity|production|demand|imports?|exports?|policy|commission|industry|sector)\b",
            r"\b(?:germany|netherlands|france|italy|spain|belgium|sweden|poland|austria)\b.{0,100}\b(?:steel|plant|mill|blast furnace|eaf|capacity|project|investment)\b",
            r"\b(?:cbam|carbon border adjustment mechanism)\b",
            r"(?:EU|欧州)(?:域内|鉄鋼|製鉄|工場|設備|生産|能力|投資|市場|需要|供給|政策|CBAM|輸入|輸出)",
        ),
        focus="CBAM and trade policy, BF-to-EAF/DRI conversion, green-steel subsidies, plant restructuring, power/hydrogen economics and import pressure",
    ),
}


def get_region_profile(slug: str) -> RegionProfile:
    key = str(slug or "").strip().lower()
    if key not in REGION_PROFILES:
        raise ValueError(f"Unsupported steel Intelligence region: {slug!r}. Choose one of {', '.join(sorted(REGION_PROFILES))}.")
    return REGION_PROFILES[key]
